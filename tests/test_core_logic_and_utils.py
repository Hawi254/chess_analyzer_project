import unittest
from unittest.mock import patch, mock_open
import os
import sys
import chess
import math # For _calculate_win_chance test verification

# Add the directory containing chess_analyzerv2.py to sys.path
# Adjust if your test file is in a different directory structure.
current_dir = os.path.dirname(os.path.abspath(__file__))
# Assuming chess_analyzerv2.py is in the parent directory of the 'tests' directory
# If chess_analyzerv2.py is in the same directory as this test file, use:
# project_dir = current_dir
project_dir = os.path.dirname(current_dir) if os.path.basename(current_dir) == "tests" else current_dir

if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from chess_analyzerv2 import ChessAnalyzer, \
    MATE_SCORE_EQUIVALENT_CP, CPL_INDIVIDUAL_EVAL_CAP_CP, \
    WIN_CHANCE_EVAL_CLAMP_CP, WIN_CHANCE_K_FACTOR

# Since we are testing utils of ChessAnalyzer, we need an instance,
# but we want to avoid its full __init__ (Stockfish, DB).
# We'll patch __init__ for these utility tests or create a "dummy" instance setup.

class TestCoreLogicAndUtils(unittest.TestCase):

    def setUp(self):
        """
        Setup a "lightweight" ChessAnalyzer instance or mock its __init__
        for testing utility methods that don't depend on full initialization.
        """
        # For most utility methods, we don't need a fully initialized Stockfish or DB.
        # We can assign constants directly or use a minimal __init__ patch.
        # Option 1: Create an instance and manually set necessary constants
        # This requires that ChessAnalyzer can be instantiated without Stockfish/DB for these utils,
        # or we patch its __init__.

        # Patching __init__ is cleaner for isolating utility method tests
        # from the complexities of full object instantiation.
        self.patch_analyzer_init = patch.object(ChessAnalyzer, '__init__', return_value=None)
        self.mock_analyzer_init = self.patch_analyzer_init.start()

        self.analyzer = ChessAnalyzer(stockfish_path="dummy_sf", depth=18) # Args don't matter due to __init__ patch

        # Manually set attributes that would normally be set by __init__ if needed by the util methods
        # These are the module-level constants copied to the instance.
        self.analyzer.MATE_SCORE_EQUIVALENT_CP = MATE_SCORE_EQUIVALENT_CP
        self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP = CPL_INDIVIDUAL_EVAL_CAP_CP
        self.analyzer.WIN_CHANCE_EVAL_CLAMP_CP = WIN_CHANCE_EVAL_CLAMP_CP
        self.analyzer.WIN_CHANCE_K_FACTOR = WIN_CHANCE_K_FACTOR
        # Add any other constants that utility methods might use via `self.`

    def tearDown(self):
        self.patch_analyzer_init.stop()

    def test_interpret_stockfish_score_dict_cp_white_turn_white_pov(self):
        score_dict = {'Centipawn': 150, 'Mate': None}
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.WHITE)
        self.assertEqual(eval_val, 150)
        self.assertFalse(is_mate)

    def test_interpret_stockfish_score_dict_cp_white_turn_black_pov(self):
        score_dict = {'Centipawn': 150, 'Mate': None} # White is +150
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.BLACK)
        self.assertEqual(eval_val, -150) # Black sees it as -150
        self.assertFalse(is_mate)

    def test_interpret_stockfish_score_dict_cp_black_turn_white_pov(self):
        score_dict = {'Centipawn': -200, 'Mate': None} # Black is -200 (i.e. White is +200)
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.BLACK, chess.WHITE)
        self.assertEqual(eval_val, 200) # White sees it as +200
        self.assertFalse(is_mate)

    def test_interpret_stockfish_score_dict_cp_black_turn_black_pov(self):
        score_dict = {'Centipawn': -50, 'Mate': None} # Black is -50
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.BLACK, chess.BLACK)
        self.assertEqual(eval_val, -50)
        self.assertFalse(is_mate)

    def test_interpret_stockfish_score_dict_mate_white_turn_white_pov_mating(self):
        score_dict = {'Mate': 3, 'Centipawn': None} # White mates in 3
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.WHITE)
        self.assertEqual(eval_val, self.analyzer.MATE_SCORE_EQUIVALENT_CP)
        self.assertTrue(is_mate)

    def test_interpret_stockfish_score_dict_mate_white_turn_black_pov_white_mating(self):
        score_dict = {'Mate': 3, 'Centipawn': None} # White mates in 3
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.BLACK)
        self.assertEqual(eval_val, -self.analyzer.MATE_SCORE_EQUIVALENT_CP) # Black is being mated
        self.assertTrue(is_mate)

    def test_interpret_stockfish_score_dict_mate_white_turn_white_pov_being_mated(self):
        score_dict = {'Mate': -2, 'Centipawn': None} # White is being mated in 2
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.WHITE)
        self.assertEqual(eval_val, -self.analyzer.MATE_SCORE_EQUIVALENT_CP)
        self.assertTrue(is_mate)

    def test_interpret_stockfish_score_dict_mate_0_fallback_to_cp(self):
        score_dict = {'Mate': 0, 'Centipawn': 75} # Mate 0 should use Centipawn
        eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.WHITE)
        self.assertEqual(eval_val, 75)
        self.assertFalse(is_mate, "Mate 0 from engine should not set is_mate=True if CP is used")

    def test_interpret_stockfish_score_dict_unexpected_format(self):
        score_dict = {'UnknownKey': 100} # Missing Mate and Centipawn
        with patch('sys.stderr') as mock_stderr: # Suppress print to stderr during test
            eval_val, is_mate = self.analyzer._interpret_stockfish_score_dict(score_dict, chess.WHITE, chess.WHITE)
            self.assertEqual(eval_val, 0.0)
            self.assertFalse(is_mate)
            self.assertTrue(mock_stderr.write.called)


    def test_cap_score_for_cpl_calculation(self):
        # Mate scores
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(self.analyzer.MATE_SCORE_EQUIVALENT_CP, True), self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(-self.analyzer.MATE_SCORE_EQUIVALENT_CP, True), -self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)
        # CP scores within cap
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(500, False), 500)
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(-300, False), -300)
        # CP scores outside cap
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP + 500, False), self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(-self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP - 200, False), -self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)
        # CP score at cap limits
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP, False), self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)
        self.assertEqual(self.analyzer._cap_score_for_cpl_calculation(-self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP, False), -self.analyzer.CPL_INDIVIDUAL_EVAL_CAP_CP)


    def test_calculate_win_chance(self):
        # Mate scores
        self.assertEqual(self.analyzer._calculate_win_chance(self.analyzer.MATE_SCORE_EQUIVALENT_CP, True), 100.0)
        self.assertEqual(self.analyzer._calculate_win_chance(-self.analyzer.MATE_SCORE_EQUIVALENT_CP, True), 0.0)
        # Zero CP score
        self.assertAlmostEqual(self.analyzer._calculate_win_chance(0, False), 50.0, places=5) # Default k-factor makes 0cp = 50%
        # Positive CP
        eval_plus_100 = 100
        expected_wc_plus_100 = 100 / (1 + math.exp(-self.analyzer.WIN_CHANCE_K_FACTOR * eval_plus_100))
        self.assertAlmostEqual(self.analyzer._calculate_win_chance(eval_plus_100, False), expected_wc_plus_100, places=5)
        # Negative CP
        eval_minus_200 = -200
        expected_wc_minus_200 = 100 / (1 + math.exp(-self.analyzer.WIN_CHANCE_K_FACTOR * eval_minus_200))
        self.assertAlmostEqual(self.analyzer._calculate_win_chance(eval_minus_200, False), expected_wc_minus_200, places=5)
        # Clamping positive
        clamped_positive_wc = 100 / (1 + math.exp(-self.analyzer.WIN_CHANCE_K_FACTOR * self.analyzer.WIN_CHANCE_EVAL_CLAMP_CP))
        self.assertAlmostEqual(self.analyzer._calculate_win_chance(self.analyzer.WIN_CHANCE_EVAL_CLAMP_CP + 1000, False), clamped_positive_wc, places=5)
        # Clamping negative
        clamped_negative_wc = 100 / (1 + math.exp(-self.analyzer.WIN_CHANCE_K_FACTOR * -self.analyzer.WIN_CHANCE_EVAL_CLAMP_CP))
        self.assertAlmostEqual(self.analyzer._calculate_win_chance(-self.analyzer.WIN_CHANCE_EVAL_CLAMP_CP - 500, False), clamped_negative_wc, places=5)
        # Max/Min values (should be between 0 and 100)
        self.assertTrue(0.0 <= self.analyzer._calculate_win_chance(100000, False) <= 100.0) # Very high positive
        self.assertTrue(0.0 <= self.analyzer._calculate_win_chance(-100000, False) <= 100.0) # Very high negative

    def test_get_processed_game_ids_no_file(self):
        """Test when the output PGN file does not exist."""
        with patch('os.path.exists', return_value=False):
            ids = self.analyzer.get_processed_game_ids("non_existent_output.pgn")
            self.assertEqual(ids, set())

    def test_get_processed_game_ids_empty_file(self):
        """Test when the output PGN file is empty."""
        with patch('builtins.open', mock_open(read_data="")) as mock_file, \
             patch('os.path.exists', return_value=True):
            ids = self.analyzer.get_processed_game_ids("empty_output.pgn")
            self.assertEqual(ids, set())
            mock_file.assert_called_once_with("empty_output.pgn", 'r', encoding='utf-8')

    def test_get_processed_game_ids_various_ids(self):
        """Test extraction of various Game ID formats."""
        mock_pgn_content = """
[Event "Lichess Game 1"]
[Site "https://lichess.org/abcdefgh"] 
[GameId "abcdefgh"] 
1. e4 *

[Event "Lichess Game 2"]
[LichessURL "https://lichess.org/ijklmnop"] 
1. d4 *

[Event "Lichess Game 3"]
[GameId "qrstuvwx"] 
1. c4 *

[Event "Chess.com Game"]
[Site "https://www.chess.com/game/live/1234567890"]
1. Nf3 *

[Event "Custom ID Game"]
[GameId "MyCustomGameID-001"]
1. g3 *

[Event "Lichess 12-char ID"]
[Site "https://lichess.org/abcdefghijkl"]
1. b3 *

[Event "Lichess 12-char GameId"]
[GameId "qrstuvwxyzab"] 
1. Nc3 *

[Event "No ID Game"]
[White "Anonymous"]
1. f4 * 
        """
        expected_ids = {"abcdefgh", "ijklmnop", "qrstuvwx", "1234567890", 
                        "MyCustomGameID-001", "abcdefghijkl", "qrstuvwxyzab"}

        with patch('builtins.open', mock_open(read_data=mock_pgn_content)) as mock_file, \
             patch('os.path.exists', return_value=True):
            ids = self.analyzer.get_processed_game_ids("dummy_output.pgn")
            self.assertEqual(ids, expected_ids)

    def test_get_processed_game_ids_ignore_case(self):
        mock_pgn_content = """
[event "Mixed Case Game"]
[site "HTTPS://LICHESS.ORG/CASESENS"]
[GameID "lowerid"]
1. e4 *
        """
        expected_ids = {"CASESENS", "lowerid"}
        with patch('builtins.open', mock_open(read_data=mock_pgn_content)) as mock_file, \
             patch('os.path.exists', return_value=True):
            ids = self.analyzer.get_processed_game_ids("dummy_output.pgn")
            self.assertEqual(ids, expected_ids)

    def test_get_processed_game_ids_id_too_long_for_specific_lichess(self):
        """Test GameId that is too long for specific Lichess patterns but caught by generic."""
        mock_pgn_content = '[GameId "ThisIsALongCustomIDNot8Or12Chars"]'
        expected_ids = {"ThisIsALongCustomIDNot8Or12Chars"}
        with patch('builtins.open', mock_open(read_data=mock_pgn_content)) as mock_file, \
             patch('os.path.exists', return_value=True):
            ids = self.analyzer.get_processed_game_ids("dummy_output.pgn")
            self.assertEqual(ids, expected_ids)

    def test_get_processed_game_ids_prevents_substring_capture(self):
        """Ensure 8-char pattern doesn't grab prefix of a longer ID in GameId tag."""
        # This was the bug previously found: "customID" from "customID123"
        mock_pgn_content = '[GameId "customID123"]' # 11 chars, should only be caught by generic
        expected_ids = {"customID123"}
        with patch('builtins.open', mock_open(read_data=mock_pgn_content)) as mock_file, \
             patch('os.path.exists', return_value=True):
            ids = self.analyzer.get_processed_game_ids("dummy_output.pgn")
            self.assertEqual(ids, expected_ids)

if __name__ == '__main__':
    unittest.main()