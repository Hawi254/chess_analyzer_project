import unittest
from unittest.mock import patch, MagicMock
import os
import sys
import chess # For chess.Board, chess.Move, chess.WHITE, chess.BLACK
import math  # For verifying Win Chance calculations if needed

# Add the directory containing chess_analyzerv2.py to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(current_dir) # Assumes tests/ is a subdirectory of project root
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from chess_analyzerv2 import ChessAnalyzer, \
    MATE_SCORE_EQUIVALENT_CP, CPL_INDIVIDUAL_EVAL_CAP_CP, \
    WIN_CHANCE_EVAL_CLAMP_CP, WIN_CHANCE_K_FACTOR, ACPL_MOVE_CPL_CAP_CP, \
    WIN_CHANCE_THRESHOLD_INACCURACY, WIN_CHANCE_THRESHOLD_MISTAKE, WIN_CHANCE_THRESHOLD_BLUNDER, \
    DEFAULT_CPL_THRESHOLD_INACCURACY, DEFAULT_CPL_THRESHOLD_MISTAKE, DEFAULT_CPL_THRESHOLD_BLUNDER, \
    WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP

MOCK_PV_LIST = ['g1f3', 'b8c6', 'd2d4'] # Default PV for mocks

class TestClassificationEngine(unittest.TestCase):

    def setUp(self):
        # Mock external dependencies not relevant to classification logic itself
        self.patch_os_exists = patch('os.path.exists', return_value=True)
        self.patch_os_access = patch('os.access', return_value=True)
        self.mock_os_exists = self.patch_os_exists.start()
        self.mock_os_access = self.patch_os_access.start()

        self.patch_stockfish_library = patch('chess_analyzerv2.Stockfish')
        self.MockStockfishClass = self.patch_stockfish_library.start()
        self.mock_stockfish_instance = MagicMock()
        self.mock_stockfish_instance.is_fen_valid.return_value = True
        self.mock_stockfish_instance.get_evaluation.return_value = {'type': 'cp', 'value': 20}
        self.mock_stockfish_instance.get_stockfish_major_version.return_value = "17"
        self.MockStockfishClass.return_value = self.mock_stockfish_instance

        self.patch_init_db = patch.object(ChessAnalyzer, '_init_db', return_value=None)
        self.mock_init_db = self.patch_init_db.start()
        
        self.depth_for_test = 18 
        self.threads_for_test = 4
        self.hash_for_test = 256

        self.analyzer = ChessAnalyzer(
            stockfish_path="dummy/path/to/stockfish",
            depth=self.depth_for_test,
            threads=self.threads_for_test,
            hash_mb=self.hash_for_test
        )
        
        # Store original thresholds to restore them if changed in a specific test
        self.original_wc_thresholds = {
            "inaccuracy": self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY,
            "mistake": self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE,
            "blunder": self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER,
        }
        self.original_cpl_thresholds = {
            "inaccuracy": self.analyzer.CPL_THRESHOLD_INACCURACY,
            "mistake": self.analyzer.CPL_THRESHOLD_MISTAKE,
            "blunder": self.analyzer.CPL_THRESHOLD_BLUNDER,
        }
        self.original_winning_leniency_threshold = self.analyzer.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP

    def tearDown(self):
        self.patch_os_exists.stop()
        self.patch_os_access.stop()
        self.patch_stockfish_library.stop()
        self.patch_init_db.stop()

        self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY = self.original_wc_thresholds["inaccuracy"]
        self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE = self.original_wc_thresholds["mistake"]
        self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER = self.original_wc_thresholds["blunder"]
        self.analyzer.CPL_THRESHOLD_INACCURACY = self.original_cpl_thresholds["inaccuracy"]
        self.analyzer.CPL_THRESHOLD_MISTAKE = self.original_cpl_thresholds["mistake"]
        self.analyzer.CPL_THRESHOLD_BLUNDER = self.original_cpl_thresholds["blunder"]
        self.analyzer.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP = self.original_winning_leniency_threshold

    def create_mock_analysis(self, centipawn=None, mate=None, pv=MOCK_PV_LIST, best_move_uci='a1a2'):
        """Helper to create a mock engine analysis dictionary."""
        analysis = {'Move': best_move_uci}
        if centipawn is not None: analysis['Centipawn'] = centipawn
        if mate is not None: analysis['Mate'] = mate
        # If pv is explicitly passed as None by the test, make it an empty list for .get('PV', [])
        analysis['PV'] = [] if pv is None else pv 
        return analysis

    # --- Comprehensive Tests for _get_move_analysis_and_comment ---

    def test_gmac_best_move_no_comment(self):
        board = chess.Board(); player_move = chess.Move.from_uci("e2e4")
        eval_before = self.create_mock_analysis(centipawn=10, best_move_uci="e2e4", pv=['e2e4', 'e7e5'])
        eval_after = self.create_mock_analysis(centipawn=-5) # Perspective of next player
        comment, cpl, eval_val, is_mate = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNone(comment); self.assertEqual(cpl, 0.0)
        self.assertEqual(eval_val, 5) # White's perspective of position after e4

    def test_gmac_no_classification_small_cpl_and_wc_loss(self):
        board = chess.Board(); player_move = chess.Move.from_uci("g1f3")
        eval_before = self.create_mock_analysis(centipawn=10, best_move_uci="e2e4") # White eval +10
        eval_after = self.create_mock_analysis(centipawn=-5) # Black's turn, eval for Black is -5 => White is +5
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNone(comment, "Expected no comment for CPL and WC loss below all thresholds")
        self.assertAlmostEqual(cpl, 5.0, delta=0.1) # raw_cpl = 10 - 5 = 5
        self.assertEqual(eval_val, 5)

    def test_gmac_player_move_better_than_engine(self):
        board = chess.Board(); player_move = chess.Move.from_uci("g1f3")
        eval_before = self.create_mock_analysis(centipawn=10, best_move_uci="e2e4") # White eval +10
        eval_after = self.create_mock_analysis(centipawn=-50) # Black's turn, eval for Black is -50 => White is +50
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNone(comment, "Player's move better than engine's, no negative comment expected")
        self.assertEqual(cpl, 0.0) # final_cpl_for_acpl is max(0, raw_cpl)
        self.assertEqual(eval_val, 50)

    def test_gmac_blunder_by_win_chance(self):
        board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"); player_move = chess.Move.from_uci("d1h5")
        eval_before = self.create_mock_analysis(centipawn=30, best_move_uci="g1f3", pv=['g1f3', 'b8c6']) # White eval +30
        eval_after = self.create_mock_analysis(centipawn=500) # Black's turn, eval for Black +500 => White is -500
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder", comment); self.assertNotIn("(CPL)", comment)
        self.assertTrue(any(sub in comment for sub in ["WC 53%→14%", "WC 53%→13%"])) # Approx
        self.assertEqual(eval_val, -500); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 530.0), delta=1)

    def test_gmac_mistake_by_win_chance(self):
        board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"); player_move = chess.Move.from_uci("a2a3")
        eval_before = self.create_mock_analysis(centipawn=30, best_move_uci="g1f3") # White eval +30
        eval_after = self.create_mock_analysis(centipawn=200) # Black's turn, eval for Black +200 => White is -200
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Mistake", comment); self.assertNotIn("(CPL)", comment)
        self.assertTrue(any(sub in comment for sub in ["WC 53%→32%", "WC 53%→33%"]))
        self.assertEqual(eval_val, -200); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 230.0), delta=1)

    def test_gmac_inaccuracy_by_win_chance(self):
        board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"); player_move = chess.Move.from_uci("h2h3")
        eval_before = self.create_mock_analysis(centipawn=30, best_move_uci="g1f3") # White eval +30
        eval_after = self.create_mock_analysis(centipawn=80) # Black's turn, eval for Black +80 => White is -80. WC Loss ~10.07%
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Inaccuracy", comment); self.assertNotIn("(CPL)", comment)
        self.assertTrue(any(sub in comment for sub in ["WC 53%→43%", "WC 53%→42%"]))
        self.assertEqual(eval_val, -80); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 110.0), delta=1)

    def test_gmac_blunder_by_cpl_when_wc_loss_small(self):
        board = chess.Board(); player_move = chess.Move.from_uci("b1a3")
        self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY = 100.0 # Bypass WC classifications
        self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE = 100.0
        self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER = 100.0
        eval_before = self.create_mock_analysis(centipawn=100, best_move_uci="e2e4") # White eval +100
        eval_after = self.create_mock_analysis(centipawn=300) # Black's turn, eval for Black +300 => White is -300. Raw CPL = 400.
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder (CPL)", comment)
        self.assertNotIn("WC", comment); self.assertEqual(eval_val, -300); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 400.0), delta=1)

    def test_gmac_mistake_by_cpl_when_wc_loss_small(self):
        board = chess.Board(); player_move = chess.Move.from_uci("b1a3")
        self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY = 100.0 
        self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE = 100.0
        self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER = 100.0
        eval_before = self.create_mock_analysis(centipawn=100, best_move_uci="e2e4") # White eval +100
        eval_after = self.create_mock_analysis(centipawn=150) # Black's turn, eval for Black +150 => White is -150. Raw CPL = 250.
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Mistake (CPL)", comment)
        self.assertNotIn("WC", comment); self.assertEqual(eval_val, -150); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 250.0), delta=1)

    def test_gmac_inaccuracy_by_cpl_when_wc_loss_small(self):
        board = chess.Board(); player_move = chess.Move.from_uci("b1a3")
        self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY = 100.0
        self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE = 100.0
        self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER = 100.0
        eval_before = self.create_mock_analysis(centipawn=50, best_move_uci="e2e4") # White eval +50
        eval_after = self.create_mock_analysis(centipawn=50) # Black's turn, eval for Black +50 => White is -50. Raw CPL = 100.
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Inaccuracy (CPL)", comment)
        self.assertNotIn("WC", comment); self.assertEqual(eval_val, -50); self.assertAlmostEqual(cpl, min(ACPL_MOVE_CPL_CAP_CP, 100.0), delta=1)

    def test_gmac_leniency_wc_blunder_not_downgraded_if_wc_too_low(self):
        board = chess.Board("R6k/8/8/8/8/8/8/K7 w - - 0 1"); player_move = chess.Move.from_uci("a8a7")
        # eval_before: +900 (WC ~96.8%), is_decisively_winning = True
        eval_before = self.create_mock_analysis(centipawn=900, best_move_uci="a1a2")
        # eval_after: +150 (WC ~64.5%), so WC_Loss = 32.3% -> Blunder by WC
        # wc_actual_player (64.5%) is NOT > 85%. So, should remain Blunder.
        eval_after = self.create_mock_analysis(centipawn=-150)
        comment, _, _, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder", comment); self.assertNotIn("Mistake", comment)

    def test_gmac_leniency_wc_blunder_downgraded_to_mistake(self):
        board = chess.Board("R6k/8/8/8/8/8/8/K7 w - - 0 1"); player_move = chess.Move.from_uci("a8a7")
        self.analyzer.WIN_CHANCE_THRESHOLD_BLUNDER = 10.0 # Temporarily lower to trigger initial Blunder easily
        self.analyzer.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP = 800 # Default
        # eval_before: +850cp (WC ~96.3%). is_decisively_winning = True.
        eval_before = self.create_mock_analysis(centipawn=850, best_move_uci="h8h7")
        # eval_after: White is +475cp (Black is -475cp). WC ~85.18%.
        # WC Loss: 96.3% - 85.18% = ~11.12%. This IS a "Blunder" with temp threshold.
        # wc_actual_player (85.18%) IS > 85.0. Downgrade to Mistake.
        eval_after = self.create_mock_analysis(centipawn=-475)
        comment, _, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Mistake", comment); self.assertNotIn("Blunder", comment)
        self.assertEqual(eval_val, 475)
        self.assertTrue(any(sub in comment for sub in ["WC 96%→85%", "WC 96%→86%"]))

    def test_gmac_leniency_wc_mistake_downgraded_to_inaccuracy(self):
        board = chess.Board("R6k/8/8/8/8/8/8/K7 w - - 0 1"); player_move = chess.Move.from_uci("a8a7")
        self.analyzer.WIN_CHANCE_THRESHOLD_MISTAKE = 5.0 # Temp lower to trigger initial Mistake
        self.analyzer.WIN_CHANCE_THRESHOLD_INACCURACY = 1.0 # Ensure it's distinct
        self.analyzer.WINNING_EVAL_THRESHOLD_FOR_LENIENCY_CP = 800

        # eval_before: +850cp (WC ~96.3%). is_decisively_winning = True.
        eval_before = self.create_mock_analysis(centipawn=850, best_move_uci="h8h7")
        # eval_after: White is +600cp (Black is -600cp). WC ~91.4%.
        # WC Loss: 96.3% - 91.4% = ~4.9%. If Mistake threshold is 5%, this is NOT a mistake.
        # Let's make WC Loss ~6%: eval_after White is +550 (WC ~89.7%) -> WC Loss ~6.6%
        eval_after = self.create_mock_analysis(centipawn=-550)
        # wc_actual_player (89.7%) IS NOT > 90.0. Stays Mistake.

        # Adjust for wc_actual_player > 90.0
        # eval_after: White is +580cp (Black is -580cp). WC ~90.7%
        # WC Loss: 96.3% - 90.7% = ~5.6%. This IS a "Mistake" with temp threshold.
        # wc_actual_player (90.7%) IS > 90.0. Downgrade to Inaccuracy.
        eval_after = self.create_mock_analysis(centipawn=-580)
        comment, _, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Inaccuracy", comment); self.assertNotIn("Mistake", comment)
        self.assertEqual(eval_val, 580)
        self.assertTrue(any(sub in comment for sub in ["WC 96%→91%", "WC 96%→90%"]))


    def test_gmac_missed_mate_is_blunder(self):
        board = chess.Board("6k1/6R1/8/8/8/8/8/K7 w - - 0 1"); player_move = chess.Move.from_uci("a1b1")
        eval_before = self.create_mock_analysis(mate=1, best_move_uci="g7g8", pv=['g7g8'])
        eval_after = self.create_mock_analysis(centipawn=-700) # White eval +700
        comment, cpl, eval_val, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder (Missed Mate)", comment)
        self.assertEqual(eval_val, 700)
        expected_raw_cpl = CPL_INDIVIDUAL_EVAL_CAP_CP - min(CPL_INDIVIDUAL_EVAL_CAP_CP, 700)
        expected_final_cpl = min(ACPL_MOVE_CPL_CAP_CP, max(0.0, expected_raw_cpl))
        self.assertAlmostEqual(cpl, expected_final_cpl, delta=1)
        self.assertNotIn("WC", comment)

    def test_gmac_comment_formatting_with_pv(self):
        board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"); player_move = chess.Move.from_uci("d1h5")
        pv_moves = ['g1f3', 'b8c6', 'd2d4']; eval_before = self.create_mock_analysis(centipawn=30, best_move_uci="g1f3", pv=pv_moves)
        eval_after = self.create_mock_analysis(centipawn=500)
        temp_board = board.copy(); san_pv_list = []
        for uci_mv in pv_moves[:3]:
            mv_obj = chess.Move.from_uci(uci_mv)
            if temp_board.is_legal(mv_obj): san_pv_list.append(temp_board.san(mv_obj)); temp_board.push(mv_obj)
            else: break
        expected_pv_san_str = f"(PV: {' '.join(san_pv_list)}...)"
        comment, _, _, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder", comment)
        self.assertIn(expected_pv_san_str, comment); self.assertIn("Best: Nf3", comment)

    def test_gmac_comment_formatting_no_pv_available(self):
        board = chess.Board("rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2"); player_move = chess.Move.from_uci("d1h5")
        eval_before = self.create_mock_analysis(centipawn=30, best_move_uci="g1f3", pv=[]) # Empty PV list
        eval_after = self.create_mock_analysis(centipawn=500)
        comment, _, _, _ = self.analyzer._get_move_analysis_and_comment(board, chess.WHITE, player_move, eval_before, eval_after)
        self.assertIsNotNone(comment); self.assertIn("Blunder", comment)
        self.assertNotIn("(PV:", comment)
        self.assertIn("Best: Nf3.", comment) # Expect period if PV is empty

if __name__ == '__main__':
    unittest.main()