# chess_analyzerv2.py
# Copyright (C) [2025] [Jasper Hawi] <jasper.hawi@gmail.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

#!/usr/bin/env python3
import chess
import chess.pgn
from stockfish import Stockfish
import argparse
import os
import sys
import math
import re
from typing import Optional, Dict, Any, Set, List, Tuple
from collections import OrderedDict
import sqlite3
import json
import signal # For graceful shutdown

# --- Configuration Constants (Defaults) ---
DEFAULT_ANALYSIS_DEPTH = 22
MATE_SCORE_EQUIVALENT_CP = 30000

# --- Lichess-aligned Constants ---
CPL_INDIVIDUAL_EVAL_CAP_CP = 1000
ACPL_MOVE_CPL_CAP_CP = 1000
WIN_CHANCE_K_FACTOR = 0.00368208
WIN_CHANCE_EVAL_CLAMP_CP = 1500
WIN_CHANCE_THRESHOLD_INACCURACY = 10.0
WIN_CHANCE_THRESHOLD_MISTAKE = 20.0
WIN_CHANCE_THRESHOLD_BLUNDER = 30.0
WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP = 800
DEFAULT_CPL_THRESHOLD_INACCURACY = 100
DEFAULT_CPL_THRESHOLD_MISTAKE = 250
DEFAULT_CPL_THRESHOLD_BLUNDER = 400

DB_CACHE_FILENAME = "chess_analyzer_cache.db"

class ChessAnalyzer:
    def __init__(self, stockfish_path: str, depth: int = DEFAULT_ANALYSIS_DEPTH,
                 threads: int = 4, hash_mb: int = 256,
                 cpl_thresh_inaccuracy: int = DEFAULT_CPL_THRESHOLD_INACCURACY,
                 cpl_thresh_mistake: int = DEFAULT_CPL_THRESHOLD_MISTAKE,
                 cpl_thresh_blunder: int = DEFAULT_CPL_THRESHOLD_BLUNDER):

        if not os.path.exists(stockfish_path):
            raise FileNotFoundError(f"Stockfish executable not found at: {stockfish_path}")
        if not os.access(stockfish_path, os.X_OK):
            raise PermissionError(f"Stockfish executable is not executable: {stockfish_path}")

        self.stockfish_path_arg = stockfish_path
        self.abs_stockfish_path = os.path.abspath(stockfish_path)
        self.analysis_depth = depth
        self.stockfish_version_for_cache = "Unknown"

        # Graceful shutdown attributes
        self.shutdown_requested = False
        self.original_sigint_handler = signal.getsignal(signal.SIGINT) # Store original SIGINT
        self.original_sigterm_handler = signal.getsignal(signal.SIGTERM) # Store original SIGTERM


        try:
            self.stockfish = Stockfish(
                path=self.stockfish_path_arg,
                depth=self.analysis_depth,
                parameters={"Threads": threads, "Hash": hash_mb}
            )
            if not self.stockfish.is_fen_valid(chess.STARTING_FEN):
                 raise RuntimeError("Stockfish engine initialized but FEN validation failed.")
            self.stockfish.set_fen_position(chess.STARTING_FEN)
            evaluation = self.stockfish.get_evaluation()
            if evaluation is None or 'type' not in evaluation or 'value' not in evaluation :
                    raise RuntimeError("Stockfish engine initialized but seems unresponsive or returns invalid evaluation.")
            
            stockfish_version_val = self.stockfish.get_stockfish_major_version()
            self.stockfish_version_for_cache = str(stockfish_version_val) if stockfish_version_val else "Unknown"
            
            print(f"Stockfish engine (Version: {self.stockfish_version_for_cache}) initialized successfully.")
            print(f"  Analysis Depth: {self.analysis_depth}, Threads: {threads}, Hash: {hash_mb}MB")

        except Exception as e:
            print(f"Error initializing Stockfish: {e}", file=sys.stderr)
            raise

        self.db_conn: Optional[sqlite3.Connection] = None
        self.db_cursor: Optional[sqlite3.Cursor] = None
        self._init_db()

        self.MATE_SCORE_EQUIVALENT_CP = MATE_SCORE_EQUIVALENT_CP
        self.CPL_INDIVIDUAL_EVAL_CAP_CP = CPL_INDIVIDUAL_EVAL_CAP_CP
        self.ACPL_MOVE_CPL_CAP_CP = ACPL_MOVE_CPL_CAP_CP
        self.WIN_CHANCE_K_FACTOR = WIN_CHANCE_K_FACTOR
        self.WIN_CHANCE_EVAL_CLAMP_CP = WIN_CHANCE_EVAL_CLAMP_CP
        self.WIN_CHANCE_THRESHOLD_INACCURACY = WIN_CHANCE_THRESHOLD_INACCURACY
        self.WIN_CHANCE_THRESHOLD_MISTAKE = WIN_CHANCE_THRESHOLD_MISTAKE
        self.WIN_CHANCE_THRESHOLD_BLUNDER = WIN_CHANCE_THRESHOLD_BLUNDER
        self.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP = WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP
        self.CPL_THRESHOLD_INACCURACY = cpl_thresh_inaccuracy
        self.CPL_THRESHOLD_MISTAKE = cpl_thresh_mistake
        self.CPL_THRESHOLD_BLUNDER = cpl_thresh_blunder

    def _handle_shutdown_signal(self, signum, frame):
        """Sets the shutdown flag when SIGINT or SIGTERM is received."""
        signal_name = signal.Signals(signum).name
        if not self.shutdown_requested:
            print(f"\nSignal {signal_name} ({signum}) received. Requesting graceful shutdown...")
            print("Will attempt to finish tasks and save data before exiting.")
            print("Press Ctrl+C again to force quit immediately (may result in data loss).")
            self.shutdown_requested = True
        else: # Second signal
            print(f"Second signal {signal_name} ({signum}) received. Forcing exit now.")
            self._restore_signal_handlers() # Attempt to restore before forceful exit
            sys.exit(1) # Force exit

    def _setup_signal_handlers(self):
        """Sets up custom signal handlers for SIGINT and SIGTERM."""
        try:
            signal.signal(signal.SIGINT, self._handle_shutdown_signal)
            signal.signal(signal.SIGTERM, self._handle_shutdown_signal)
        except ValueError as e: # Can happen if not in main thread, e.g. some test runners
            print(f"Warning: Could not set custom signal handlers: {e}. Graceful shutdown via signals might not work.", file=sys.stderr)
        except Exception as e: # Catch any other unexpected errors
            print(f"Warning: Unexpected error setting signal handlers: {e}", file=sys.stderr)


    def _restore_signal_handlers(self):
        """Restores original signal handlers."""
        try:
            if self.original_sigint_handler is not None:
                signal.signal(signal.SIGINT, self.original_sigint_handler)
            if self.original_sigterm_handler is not None:
                signal.signal(signal.SIGTERM, self.original_sigterm_handler)
        except ValueError:
            # print("Warning: Could not restore signal handlers (e.g. if already in a non-main thread or invalid handler).", file=sys.stderr)
            pass # Suppress error if handler cannot be restored (e.g. if already exited or thread issues)
        except Exception as e:
            print(f"Warning: Error restoring signal handlers: {e}", file=sys.stderr)


    def _init_db(self):
        try:
            self.db_conn = sqlite3.connect(DB_CACHE_FILENAME)
            self.db_cursor = self.db_conn.cursor()
            self.db_cursor.execute("""
                CREATE TABLE IF NOT EXISTS fen_analysis_cache (
                    fen TEXT NOT NULL,
                    analysis_depth INTEGER NOT NULL,
                    stockfish_path_abs TEXT NOT NULL,
                    stockfish_version TEXT NOT NULL,
                    analysis_result_json TEXT NOT NULL,
                    PRIMARY KEY (fen, analysis_depth, stockfish_path_abs, stockfish_version)
                )
            """)
            self.db_conn.commit()
            print(f"SQLite cache database '{DB_CACHE_FILENAME}' initialized/connected.")
        except sqlite3.Error as e:
            print(f"Error initializing SQLite database {DB_CACHE_FILENAME}: {e}", file=sys.stderr)
            self.db_conn = None; self.db_cursor = None
            raise RuntimeError(f"Failed to initialize SQLite cache: {e}")

    def _get_cached_analysis(self, fen: str) -> Optional[Dict[str, Any]]:
        if not self.db_cursor or not self.db_conn: return None
        try:
            self.db_cursor.execute("""
                SELECT analysis_result_json FROM fen_analysis_cache
                WHERE fen = ? AND analysis_depth = ? AND stockfish_path_abs = ? AND stockfish_version = ?
            """, (fen, self.analysis_depth, self.abs_stockfish_path, self.stockfish_version_for_cache))
            row = self.db_cursor.fetchone()
            if row: return json.loads(row[0])
        except sqlite3.Error as e: print(f"  Warning: SQLite error during cache lookup for FEN {fen}: {e}", file=sys.stderr)
        except json.JSONDecodeError as e: print(f"  Warning: JSON decode error for cached FEN {fen}: {e}", file=sys.stderr)
        return None

    def _store_analysis_in_cache(self, fen: str, analysis_result: Optional[Dict[str, Any]]):
        if not self.db_cursor or not self.db_conn: return
        try:
            analysis_json = json.dumps(analysis_result)
            self.db_cursor.execute("""
                INSERT OR REPLACE INTO fen_analysis_cache 
                (fen, analysis_depth, stockfish_path_abs, stockfish_version, analysis_result_json)
                VALUES (?, ?, ?, ?, ?)
            """, (fen, self.analysis_depth, self.abs_stockfish_path, self.stockfish_version_for_cache, analysis_json))
            self.db_conn.commit()
        except sqlite3.Error as e: print(f"  Warning: SQLite error during cache store for FEN {fen}: {e}", file=sys.stderr)
        except TypeError as e: print(f"  Warning: JSON Type error for caching FEN {fen}: {e}", file=sys.stderr)

    def _interpret_stockfish_score_dict(self, score_dict: Dict[str, Any],
                                         current_turn_on_board: chess.Color,
                                         perspective_for_player: chess.Color) -> Tuple[float, bool]:
        raw_score_current_mover_persp = 0.0; is_mate = False
        if score_dict.get('Mate') is not None:
            mate_in_x = score_dict['Mate']
            if mate_in_x == 0: raw_score_current_mover_persp = float(score_dict.get('Centipawn', 0.0))
            else: raw_score_current_mover_persp = self.MATE_SCORE_EQUIVALENT_CP if mate_in_x > 0 else -self.MATE_SCORE_EQUIVALENT_CP; is_mate = True
        elif score_dict.get('Centipawn') is not None: raw_score_current_mover_persp = float(score_dict['Centipawn'])
        else: print(f"  Warning: Unexpected score_dict format: {score_dict}", file=sys.stderr); return 0.0, False
        return (raw_score_current_mover_persp if perspective_for_player == current_turn_on_board else -raw_score_current_mover_persp), is_mate

    def _cap_score_for_cpl_calculation(self, score_player_perspective: float, is_mate: bool) -> float:
        if is_mate: return self.CPL_INDIVIDUAL_EVAL_CAP_CP if score_player_perspective > 0 else -self.CPL_INDIVIDUAL_EVAL_CAP_CP
        return min(self.CPL_INDIVIDUAL_EVAL_CAP_CP, max(-self.CPL_INDIVIDUAL_EVAL_CAP_CP, score_player_perspective))

    def _calculate_win_chance(self, score_player_perspective: float, is_mate: bool) -> float:
        if is_mate: return 100.0 if score_player_perspective > 0 else 0.0
        clamped_score = min(self.WIN_CHANCE_EVAL_CLAMP_CP, max(-self.WIN_CHANCE_EVAL_CLAMP_CP, score_player_perspective))
        try: win_chance = 100 / (1 + math.exp(-self.WIN_CHANCE_K_FACTOR * clamped_score))
        except OverflowError: win_chance = 100.0 if clamped_score > 0 else 0.0
        return min(100.0, max(0.0, win_chance))

    def _get_move_analysis_and_comment(
        self, board_before_player_move: chess.Board, player_who_moved: chess.Color,
        actual_player_move_obj: chess.Move, engine_analysis_of_pos_before_move: Dict[str, Any],
        engine_analysis_of_pos_after_move: Dict[str, Any]
    ) -> Tuple[Optional[str], float, float, bool]:
        comment: Optional[str] = None; final_cpl_for_acpl: float = 0.0

        eval_engine_best_player_persp, is_mate_engine_best = self._interpret_stockfish_score_dict(
            engine_analysis_of_pos_before_move, board_before_player_move.turn, player_who_moved)
        engine_best_move_uci = engine_analysis_of_pos_before_move['Move']
        engine_best_move_pv_uci_list = engine_analysis_of_pos_before_move.get('PV', [])

        board_after_player_move_turn = not player_who_moved
        eval_actual_player_persp, is_mate_actual_player = self._interpret_stockfish_score_dict(
            engine_analysis_of_pos_after_move, board_after_player_move_turn, player_who_moved)
        
        if actual_player_move_obj.uci() == engine_best_move_uci and \
           not (is_mate_engine_best and eval_engine_best_player_persp > 0 and not (is_mate_actual_player and eval_actual_player_persp > 0)):
            return None, 0.0, eval_actual_player_persp, is_mate_actual_player

        capped_eval_engine_best = self._cap_score_for_cpl_calculation(eval_engine_best_player_persp, is_mate_engine_best)
        capped_eval_actual_player = self._cap_score_for_cpl_calculation(eval_actual_player_persp, is_mate_actual_player)
        raw_cpl = capped_eval_engine_best - capped_eval_actual_player
        final_cpl_for_acpl = min(self.ACPL_MOVE_CPL_CAP_CP, max(0.0, raw_cpl))

        wc_engine_best = self._calculate_win_chance(eval_engine_best_player_persp, is_mate_engine_best)
        wc_actual_player = self._calculate_win_chance(eval_actual_player_persp, is_mate_actual_player)
        win_chance_loss_percent = max(0.0, wc_engine_best - wc_actual_player)

        classification_type: Optional[str] = None
        if is_mate_engine_best and eval_engine_best_player_persp > 0 and not (is_mate_actual_player and eval_actual_player_persp > 0):
            classification_type = "Blunder (Missed Mate)"
        
        is_decisively_winning_before_move = eval_engine_best_player_persp > self.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP and not is_mate_engine_best

        if classification_type is None:
            if win_chance_loss_percent >= self.WIN_CHANCE_THRESHOLD_BLUNDER:
                if is_decisively_winning_before_move and wc_actual_player > 85.0 and raw_cpl < (self.CPL_INDIVIDUAL_EVAL_CAP_CP * 1.5): classification_type = "Mistake"
                else: classification_type = "Blunder"
            elif win_chance_loss_percent >= self.WIN_CHANCE_THRESHOLD_MISTAKE:
                if is_decisively_winning_before_move and wc_actual_player > 90.0 and raw_cpl < self.CPL_INDIVIDUAL_EVAL_CAP_CP: classification_type = "Inaccuracy"
                else: classification_type = "Mistake"
            elif win_chance_loss_percent >= self.WIN_CHANCE_THRESHOLD_INACCURACY:
                classification_type = "Inaccuracy"

        if classification_type is None and not is_decisively_winning_before_move and raw_cpl > 0:
            if raw_cpl >= self.CPL_THRESHOLD_BLUNDER: classification_type = "Blunder (CPL)"
            elif raw_cpl >= self.CPL_THRESHOLD_MISTAKE: classification_type = "Mistake (CPL)"
            elif raw_cpl >= self.CPL_THRESHOLD_INACCURACY: classification_type = "Inaccuracy (CPL)"
        
        if classification_type:
            if "(CPL)" in classification_type and final_cpl_for_acpl < 1: comment = None; final_cpl_for_acpl = 0.0
            else:
                try: best_move_san = board_before_player_move.san(chess.Move.from_uci(engine_best_move_uci))
                except Exception: best_move_san = engine_best_move_uci
                pv_san_str = ""
                if engine_best_move_pv_uci_list:
                    temp_board_for_pv = board_before_player_move.copy()
                    san_pv_list = []
                    try:
                        for i, pv_move_uci in enumerate(engine_best_move_pv_uci_list):
                            if i >= 3: break
                            pv_move_obj = chess.Move.from_uci(pv_move_uci)
                            if temp_board_for_pv.is_legal(pv_move_obj):
                                san_pv_list.append(temp_board_for_pv.san(pv_move_obj)); temp_board_for_pv.push(pv_move_obj)
                            else: san_pv_list.append(pv_move_uci + "?"); break
                        if san_pv_list: pv_san_str = f" (PV: {' '.join(san_pv_list)}...)"
                    except Exception: pv_san_str = f" (PV: {' '.join(engine_best_move_pv_uci_list[:3])}...)"
                details_parts = []
                if raw_cpl >= 1 or raw_cpl <= -1: details_parts.append(f"{-raw_cpl:.0f}cp")
                if classification_type not in ["Blunder (Missed Mate)"] and "(CPL)" not in classification_type and win_chance_loss_percent >= 1: # Don't add WC if it's Missed Mate
                    details_parts.append(f"WC {wc_engine_best:.0f}%→{wc_actual_player:.0f}% [-{win_chance_loss_percent:.0f}%]")
                details_str = f" ({', '.join(details_parts)})" if details_parts else ""
                comment = f"{classification_type}{details_str}. Best: {best_move_san}{pv_san_str}"
        return comment, final_cpl_for_acpl, eval_actual_player_persp, is_mate_actual_player

    def get_processed_game_ids(self, output_pgn_path: str) -> Set[str]:
        processed_ids: Set[str] = set()
        if not os.path.exists(output_pgn_path): return processed_ids
        try:
            with open(output_pgn_path, 'r', encoding='utf-8') as pgn_file: content = pgn_file.read()
            patterns_to_try = [
                r'\[(?:Site|LichessURL)\s+"https?://lichess\.org/([a-zA-Z0-9]{8,12})[^"]*"',
                r'\[Site\s+"https?://www\.chess\.com/game/live/([0-9]+)"',
                r'\[GameId\s+"([a-zA-Z0-9]{8}(?!\w))"',
                r'\[GameId\s+"([a-zA-Z0-9]{12}(?!\w))"',
                r'\[GameId\s+"([^"]+)"'
            ]
            for pattern_str in patterns_to_try:
                for match in re.finditer(pattern_str, content, re.IGNORECASE):
                    game_id = match.group(1)
                    if pattern_str == r'\[GameId\s+"([^"]+)"' and \
                       (((len(game_id) == 8 or len(game_id) == 12) and game_id.isalnum()) and game_id in processed_ids):
                        continue
                    processed_ids.add(game_id)
        except Exception as e: print(f"Error reading processed game IDs: {e}", file=sys.stderr)
        return processed_ids

    def batch_analyze_positions(self, fen_list: List[str]) -> Dict[str, Optional[Dict[str, Any]]]:
        results: Dict[str, Optional[Dict[str, Any]]] = {};
        if not fen_list: return results
        print(f"  Starting Stockfish batch analysis of {len(fen_list)} FENs...")
        for i, fen in enumerate(fen_list):
            if self.shutdown_requested: # Check flag within long loop
                print("    Batch analysis interrupted by shutdown signal.")
                break
            if (i + 1) % 10 == 0 or i == len(fen_list) -1 : print(f"    Analyzing FEN {i+1}/{len(fen_list)} with Stockfish...", end='\r' if i != len(fen_list) -1 else '\n', flush=True)
            try:
                self.stockfish.set_fen_position(fen); top_moves = self.stockfish.get_top_moves(1)
                if top_moves: results[fen] = top_moves[0]
                else: results[fen] = None
            except Exception as e: print(f"  Warning: Stockfish error for FEN {fen}: {e}", file=sys.stderr); results[fen] = None
        print(f"  Stockfish batch FEN processing finished ({len(results)} FENs analyzed out of {len(fen_list)}).")
        return results

    def run_analysis(self, input_pgn_path: str, output_pgn_path: str, target_player_name: Optional[str] = None, pgn_columns: int = 80):
        if not self.db_conn or not self.db_cursor:
            print("Error: Database not initialized. Cannot run analysis.", file=sys.stderr)
            return

        self._setup_signal_handlers() # Setup custom handlers

        processed_ids = self.get_processed_game_ids(output_pgn_path)
        stats = {"read":0, "analyzed":0, "skipped_processed":0, "skipped_no_id":0, "errors":0,
                 "fen_cache_hits": 0, "fens_analyzed_by_stockfish": 0 }
        
        try:
            with open(input_pgn_path, 'r', encoding='utf-8', errors='replace') as infile, \
                 open(output_pgn_path, 'a', encoding='utf-8') as outfile:
                game_count_in_current_run = 0
                while True: 
                    if self.shutdown_requested:
                        print("Graceful shutdown initiated: Stopping before processing next game.")
                        break

                    current_file_pos_headers = infile.tell()
                    try: headers = chess.pgn.read_headers(infile)
                    except Exception as e:
                        if self.shutdown_requested: break
                        print(f"  Critical error reading headers: {e}", file=sys.stderr); infile.seek(current_file_pos_headers); line_num = 0; skipped_game = False
                        while True:
                            if self.shutdown_requested: break
                            line = infile.readline(); line_num += 1
                            if not line: skipped_game = True; break
                            if line.strip().startswith("[Event ") and line_num > 1: infile.seek(infile.tell() - len(line.encode('utf-8'))); skipped_game = True; break
                            if line_num > 1000: print("  Skipped too many lines.", file=sys.stderr); skipped_game = True; break
                        if not line or skipped_game or self.shutdown_requested: break; continue
                    except EOFError: break
                    if headers is None: break
                    if self.shutdown_requested: break
                    stats["read"] += 1
                    
                    # ... (Game ID extraction, player name extraction, skipping logic) ...
                    site_url = headers.get("Site", ""); game_id_tag = headers.get("GameId", ""); lichess_url_tag = headers.get("LichessURL", "")
                    game_id_processed: Optional[str] = None
                    if "lichess.org/" in site_url: match = re.search(r"lichess\.org/([a-zA-Z0-9]{8,12})", site_url); game_id_processed = match.group(1) if match else None
                    elif "chess.com/game/live/" in site_url: match = re.search(r"chess\.com/game/live/([0-9]+)", site_url); game_id_processed = match.group(1) if match else None
                    elif game_id_tag: game_id_processed = game_id_tag
                    elif "lichess.org/" in lichess_url_tag: match = re.search(r"lichess\.org/([a-zA-Z0-9]{8,12})", lichess_url_tag); game_id_processed = match.group(1) if match else None
                    white_player_actual = headers.get("White", "White"); black_player_actual = headers.get("Black", "Black"); event = headers.get("Event", "Unk. Event")
                    print(f"\nProcessing game {stats['read']}: {event[:40]} - {white_player_actual} vs {black_player_actual} (ID: {game_id_processed or 'N/A'})")
                    if not game_id_processed: print("  Skipping game: No usable GameId found."); stats["skipped_no_id"] += 1; infile.seek(current_file_pos_headers); chess.pgn.read_game(infile); continue
                    if game_id_processed in processed_ids: print(f"  Skipping game (ID: {game_id_processed}): Already processed."); stats["skipped_processed"] += 1; infile.seek(current_file_pos_headers); chess.pgn.read_game(infile); continue
                    if self.shutdown_requested: print("Graceful shutdown: Current game will not be processed."); break
                    
                    infile.seek(current_file_pos_headers); game = chess.pgn.read_game(infile)
                    if game is None:
                        print("  Error: Could not parse game data.", file=sys.stderr); stats["errors"]+=1 # ... (rest of malformed game skip) ...
                        continue
                    
                    all_move_details: List[Dict[str, Any]] = []; game_unique_fens: OrderedDict[str, None] = OrderedDict(); current_board = game.board()
                    for node in game.mainline():
                        if self.shutdown_requested: break # Check inside inner loop too
                        move = node.move;
                        if move is None: continue
                        fen_before_move = current_board.fen(); board_state_before_this_move_for_storage = current_board.copy()
                        current_board.push(move); fen_after_move = current_board.fen()
                        all_move_details.append({"pgn_node": node, "actual_move_obj": move, "board_before_move_copy": board_state_before_this_move_for_storage,
                                                 "fen_before_move": fen_before_move, "fen_after_move": fen_after_move})
                        game_unique_fens[fen_before_move] = None; game_unique_fens[fen_after_move] = None
                    if self.shutdown_requested: print("Graceful shutdown: Stopped during move collection."); break
                    
                    if not all_move_details: # Handle empty game
                        # ... (empty game handling as before) ...
                        continue
                    
                    fens_to_actually_analyze: List[str] = []; current_game_analysis_results: Dict[str, Optional[Dict[str, Any]]] = {}
                    for fen_key in game_unique_fens.keys():
                        if self.shutdown_requested: break
                        cached_result = self._get_cached_analysis(fen_key)
                        if cached_result is not None: current_game_analysis_results[fen_key] = cached_result; stats["fen_cache_hits"] += 1
                        else: fens_to_actually_analyze.append(fen_key)
                    if self.shutdown_requested: print("Graceful shutdown: Stopped before batch analysis."); break
                    
                    if fens_to_actually_analyze:
                        print(f"  Cache miss for {len(fens_to_actually_analyze)}/{len(game_unique_fens)} unique FENs. Analyzing with Stockfish.")
                        newly_analyzed_infos = self.batch_analyze_positions(fens_to_actually_analyze) # This method now also checks shutdown_requested
                        if self.shutdown_requested and len(newly_analyzed_infos) < len(fens_to_actually_analyze):
                            print("Graceful shutdown: Batch analysis was interrupted. Partially analyzed FENs may not be cached for this game.")
                        stats["fens_analyzed_by_stockfish"] += len(newly_analyzed_infos) # Count actual results
                        for fen_key, analysis_info in newly_analyzed_infos.items():
                            current_game_analysis_results[fen_key] = analysis_info
                            if not self.shutdown_requested or fen_key in fens_to_actually_analyze[:len(newly_analyzed_infos)] : # Only store if fully analyzed before potential interrupt in batch
                                self._store_analysis_in_cache(fen_key, analysis_info)
                    else: print(f"  All {len(game_unique_fens)} unique FENs for this game found in cache.")
                    if self.shutdown_requested: print("Graceful shutdown: Stopped after batch analysis/caching."); break
                    
                    white_cpls: List[float] = []; black_cpls: List[float] = []; white_move_count = 0; black_move_count = 0
                    for move_detail in all_move_details:
                        if self.shutdown_requested: break # Check inside annotation loop
                        # ... (Annotation logic as before, using current_game_analysis_results) ...
                        pgn_node = move_detail["pgn_node"]; actual_move = move_detail["actual_move_obj"]
                        board_before_move = move_detail["board_before_move_copy"]; player_who_moved = board_before_move.turn
                        fen_before = move_detail["fen_before_move"]; fen_after = move_detail["fen_after_move"]
                        analysis_of_current_pos = current_game_analysis_results.get(fen_before)
                        analysis_of_next_pos = current_game_analysis_results.get(fen_after)
                        
                        # --- OLD Line with SyntaxError ---
                        # move_san_for_warning = "N/A"; try: move_san_for_warning = board_before_move.san(actual_move)
                        # except: move_san_for_warning = actual_move.uci()

                        # --- CORRECTED Block ---
                        move_san_for_warning = "N/A" 
                        try:
                            move_san_for_warning = board_before_move.san(actual_move)
                        except Exception: # Be more specific if you know the exact exceptions, e.g., chess.IllegalMoveError
                            move_san_for_warning = actual_move.uci()
                        # --- End CORRECTION ---

                        if not analysis_of_current_pos: print(f"    Warning: No analysis for FEN_before: {fen_before} for move {board_before_move.fullmove_number}{'.' if player_who_moved == chess.WHITE else '...'}{move_san_for_warning}", file=sys.stderr); continue
                        # ... (rest of the loop)
                        if not analysis_of_next_pos:
                            temp_board_check = board_before_move.copy(); temp_board_check.push(actual_move)
                            if temp_board_check.is_checkmate(): analysis_of_next_pos = {'Move': None, 'Centipawn': None, 'Mate': -1 if player_who_moved == chess.WHITE else 1}
                            elif temp_board_check.is_stalemate(): analysis_of_next_pos = {'Move': None, 'Centipawn': 0, 'Mate': None}
                        if not analysis_of_next_pos: print(f"    Warning: No analysis for FEN_after: {fen_after} ...", file=sys.stderr); continue
                        comment_text, cpl_val, eval_after_val, is_mate_after = self._get_move_analysis_and_comment(
                            board_before_move, player_who_moved, actual_move, analysis_of_current_pos, analysis_of_next_pos)
                        eval_comment_part = ""
                        if is_mate_after:
                            mate_val_for_eval_tag = 0
                            if analysis_of_next_pos.get("Mate") is not None: mate_val_for_eval_tag = -analysis_of_next_pos["Mate"]
                            if mate_val_for_eval_tag != 0: eval_comment_part = f"{{[%eval #{mate_val_for_eval_tag},{self.analysis_depth}]}}"
                        else: eval_comment_part = f"{{[%eval {eval_after_val/100.0:.2f},{self.analysis_depth}]}}"
                        pgn_node.comment = (pgn_node.comment + " " + eval_comment_part).strip() if pgn_node.comment else eval_comment_part
                        should_add_classification_comment = (not target_player_name or (target_player_name.lower() == (white_player_actual if player_who_moved == chess.WHITE else black_player_actual).lower()))
                        if comment_text and should_add_classification_comment:
                            pgn_node.comment = (pgn_node.comment + f" {{ {comment_text} }}").strip()
                            print(f"    {board_before_move.fullmove_number}{'.' if player_who_moved == chess.WHITE else '...'} {move_san_for_warning}: {comment_text.split('.')[0]}")
                        elif comment_text: print(f"    {board_before_move.fullmove_number}{'.' if player_who_moved == chess.WHITE else '...'} {move_san_for_warning} (Opponent): Classified {comment_text.split('.')[0]} - not added to PGN")
                        if player_who_moved == chess.WHITE: white_move_count += 1; white_cpls.append(cpl_val)
                        else: black_move_count += 1; black_cpls.append(cpl_val)
                    if self.shutdown_requested: print("Graceful shutdown: Stopped during annotation."); break
                    
                    def calculate_acpl_str(cpls, count): # Corrected ACPL calculation
                        if count == 0: return "N/A"
                        valid_cpls = [c for c in cpls if c > 0]
                        if not valid_cpls : return "0.0"
                        return f"{sum(valid_cpls) / count:.1f}"
                    # ... (ACPL header setting, PGN export) ...
                    game.headers["WhiteACPL"] = calculate_acpl_str(white_cpls, white_move_count)
                    game.headers["BlackACPL"] = calculate_acpl_str(black_cpls, black_move_count)
                    print(f"  White ACPL: {game.headers['WhiteACPL']} ({white_move_count} moves, {len([c for c in white_cpls if c>0])} CPLd)")
                    print(f"  Black ACPL: {game.headers['BlackACPL']} ({black_move_count} moves, {len([c for c in black_cpls if c>0])} CPLd)")
                    if target_player_name:
                        if target_player_name.lower() == white_player_actual.lower(): print(f"  Target Player ({white_player_actual}) ACPL: {game.headers['WhiteACPL']}")
                        elif target_player_name.lower() == black_player_actual.lower(): print(f"  Target Player ({black_player_actual}) ACPL: {game.headers['BlackACPL']}")
                    if "GameId" not in game.headers and game_id_processed: game.headers["GameId"] = game_id_processed
                    
                    exporter = chess.pgn.StringExporter(headers=True, variations=True, comments=True, columns=pgn_columns)
                    pgn_game_string = game.accept(exporter)
                    if game_count_in_current_run > 0 and outfile.tell() > 0: outfile.write("\n")
                    outfile.write(pgn_game_string)
                    if pgn_game_string.strip(): outfile.write("\n")
                    outfile.flush(); game_count_in_current_run +=1; stats["analyzed"] += 1
                    if game_id_processed: processed_ids.add(game_id_processed)

        except KeyboardInterrupt:
            print("\nKeyboardInterrupt received by run_analysis. Setting shutdown flag.")
            self.shutdown_requested = True
        except Exception as e:
            print(f"An unexpected error occurred in run_analysis: {e}", file=sys.stderr)
            import traceback; traceback.print_exc()
        finally:
            if self.shutdown_requested:
                print("Finalizing due to shutdown request...")
            if hasattr(self, 'stockfish') and self.stockfish:
                 print("\nStockfish engine processing has concluded.") # Simpler message
            if self.db_conn:
                try:
                    self.db_conn.commit() 
                    self.db_conn.close()
                    print(f"SQLite database connection to '{DB_CACHE_FILENAME}' closed.")
                except sqlite3.Error as e: print(f"Error finalizing SQLite database: {e}", file=sys.stderr)
            self._restore_signal_handlers()
            # ... (Print summary) ...
            print("\n--- Analysis Summary ---"); print(f"Total games read: {stats['read']}"); print(f"Games newly analyzed: {stats['analyzed']}")
            print(f"Skipped (already processed): {stats['skipped_processed']}"); print(f"Skipped (no GameId): {stats['skipped_no_id']}")
            print(f"Errors during processing: {stats['errors']}");
            print(f"FEN cache hits during run: {stats['fen_cache_hits']}"); print(f"FENs analyzed by Stockfish: {stats['fens_analyzed_by_stockfish']}")
            print(f"Output PGN: '{output_pgn_path}'"); print(f"Cache database used: '{DB_CACHE_FILENAME}'")

def main():
    # ... (main function argument parsing remains the same) ...
    parser = argparse.ArgumentParser(description="Analyzes chess games from PGN using Stockfish.", formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("input_pgn", help="Path to input PGN file.")
    parser.add_argument("output_pgn", help="Path to save/append annotated PGN file.")
    parser.add_argument("--player_name", help="Optional: Player name to focus annotations for.", default=None)
    parser.add_argument("--stockfish_path", required=True, help="Path to Stockfish executable.")
    parser.add_argument("--depth", type=int, default=DEFAULT_ANALYSIS_DEPTH, help="Stockfish analysis depth.")
    default_threads = max(1, (os.cpu_count() or 2) // 2)
    parser.add_argument("--threads", type=int, default=default_threads, help="Stockfish CPU threads.")
    parser.add_argument("--hash", type=int, default=128, help="Stockfish hash memory (MB).")
    parser.add_argument("--cpl_inaccuracy", type=int, default=DEFAULT_CPL_THRESHOLD_INACCURACY, help="CPL for Inaccuracy.")
    parser.add_argument("--cpl_mistake", type=int, default=DEFAULT_CPL_THRESHOLD_MISTAKE, help="CPL for Mistake.")
    parser.add_argument("--cpl_blunder", type=int, default=DEFAULT_CPL_THRESHOLD_BLUNDER, help="CPL for Blunder.")
    parser.add_argument("--pgn_columns", type=int, default=80, help="PGN move text wrapping width.")
    if len(sys.argv) == 1: parser.print_help(sys.stderr); sys.exit(1)
    args = parser.parse_args()

    analyzer = None # Define analyzer here to ensure it's in scope for finally block in main
    try:
        analyzer = ChessAnalyzer(stockfish_path=args.stockfish_path, depth=args.depth, threads=args.threads, hash_mb=args.hash,
                                 cpl_thresh_inaccuracy=args.cpl_inaccuracy, cpl_thresh_mistake=args.cpl_mistake, cpl_thresh_blunder=args.cpl_blunder)
        analyzer.run_analysis(args.input_pgn, args.output_pgn, args.player_name, args.pgn_columns)
    except (FileNotFoundError, PermissionError, RuntimeError) as e:
        print(f"Initialization or Runtime Error: {e}", file=sys.stderr)
        if analyzer: analyzer._restore_signal_handlers() # Attempt to restore if analyzer was created
        sys.exit(1)
    except KeyboardInterrupt: # Catch Ctrl+C at the main level too
        print("\nKeyboardInterrupt received at main level. Shutting down gracefully...")
        if analyzer:
            analyzer.shutdown_requested = True
            # The finally block in run_analysis should handle cleanup if it was running
            # If KeyboardInterrupt happens before run_analysis even starts or after it finishes,
            # we still want to ensure resources are cleaned if analyzer object exists.
            if analyzer.db_conn:
                try:
                    analyzer.db_conn.commit()
                    analyzer.db_conn.close()
                    print(f"SQLite database connection to '{DB_CACHE_FILENAME}' closed during main KBI.")
                except sqlite3.Error as e: print(f"Error closing SQLite from main KBI: {e}", file=sys.stderr)
            analyzer._restore_signal_handlers()
    except Exception as e:
        print(f"A critical error occurred: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        if analyzer: analyzer._restore_signal_handlers()
        sys.exit(1)
    # No explicit finally needed here in main if run_analysis's finally handles resource cleanup well
    # and KeyboardInterrupt is handled to also trigger cleanup.

if __name__ == "__main__":
    main()