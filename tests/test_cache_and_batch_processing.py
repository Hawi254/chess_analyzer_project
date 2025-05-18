import unittest
from unittest.mock import patch, MagicMock, Mock
import os
import sys
import sqlite3
import json
import chess # Only for chess.STARTING_FEN in batch_analyze_positions test, if needed

# Add the directory containing chess_analyzerv2.py to sys.path
current_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(current_dir) # Assumes tests/ is a subdirectory of project root
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

from chess_analyzerv2 import ChessAnalyzer, DB_CACHE_FILENAME # Import DB_CACHE_FILENAME for potential use

# Use a dedicated in-memory DB for these tests or a temporary file
TEST_DB_NAME = ":memory:"
# TEST_DB_NAME = "test_cache.db" # Alternative: use a file, ensure cleanup in tearDownClass

class TestCacheAndBatchProcessing(unittest.TestCase):

    def setUp(self):
        # Mock external dependencies not relevant to cache/batch logic itself for analyzer instantiation
        self.patch_os_exists_sf = patch('os.path.exists', return_value=True) # For stockfish path
        self.patch_os_access_sf = patch('os.access', return_value=True)   # For stockfish path
        self.mock_os_exists_sf = self.patch_os_exists_sf.start()
        self.mock_os_access_sf = self.patch_os_access_sf.start()

        self.patch_stockfish_library = patch('chess_analyzerv2.Stockfish')
        self.MockStockfishClass = self.patch_stockfish_library.start()
        self.mock_stockfish_instance = MagicMock()
        self.mock_stockfish_instance.is_fen_valid.return_value = True
        self.mock_stockfish_instance.get_evaluation.return_value = {'type': 'cp', 'value': 20}
        self.mock_stockfish_instance.get_stockfish_major_version.return_value = "17_test" # Distinct version for testing
        self.MockStockfishClass.return_value = self.mock_stockfish_instance
        
        # For these tests, we WANT _init_db to run and set up a real (in-memory) DB.
        # So, we do NOT patch _init_db here.
        # Instead, we'll ensure the DB_CACHE_FILENAME is set to our test DB.

        self.analyzer = ChessAnalyzer(
            stockfish_path="dummy/sf_for_cache_test", # Make it distinct for these tests
            depth=19, # Distinct depth for these tests
            threads=1,
            hash_mb=32
        )
        # Override the DB_CACHE_FILENAME for this test instance to use in-memory
        # This is a bit of a hack; ideally ChessAnalyzer would take db_path as an arg
        # For now, we ensure _init_db uses a predictable name or in-memory.
        # The ChessAnalyzer already uses DB_CACHE_FILENAME module constant.
        # To force in-memory for testing _init_db directly:
        if self.analyzer.db_conn: # Close connection made with default DB_CACHE_FILENAME
            self.analyzer.db_conn.close()
        
        self.analyzer.db_conn = sqlite3.connect(TEST_DB_NAME) # Reconnect to in-memory
        self.analyzer.db_cursor = self.analyzer.db_conn.cursor()
        # Manually call _init_db's table creation part if it didn't run fully,
        # or ensure _init_db can be called again safely.
        # For simplicity, let's assume _init_db can create table if not exists.
        self.analyzer._init_db() # Call it again to ensure table on TEST_DB_NAME

        # Store some common parameters for cache key matching
        self.test_fen1 = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
        self.test_fen2 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq e3 0 1"
        self.analysis_result1 = {"Move": "e2e4", "Centipawn": 10, "PV": ["e2e4", "e7e5"]}
        self.analysis_result_none = None # For testing storing None


    def tearDown(self):
        if self.analyzer.db_conn:
            self.analyzer.db_conn.close()
            self.analyzer.db_conn = None # Ensure it's reset for next test's setUp
            self.analyzer.db_cursor = None
        # If using a temporary file DB:
        # if TEST_DB_NAME != ":memory:" and os.path.exists(TEST_DB_NAME):
        #     os.remove(TEST_DB_NAME)

        self.patch_os_exists_sf.stop()
        self.patch_os_access_sf.stop()
        self.patch_stockfish_library.stop()

    # --- Tests for SQLite Caching Methods ---

    def test_db_init_creates_table(self):
        """Test if _init_db creates the fen_analysis_cache table."""
        self.assertIsNotNone(self.analyzer.db_conn)
        self.assertIsNotNone(self.analyzer.db_cursor)
        try:
            self.analyzer.db_cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='fen_analysis_cache';")
            table = self.analyzer.db_cursor.fetchone()
            self.assertIsNotNone(table, "fen_analysis_cache table was not created.")
            self.assertEqual(table[0], "fen_analysis_cache")
        except sqlite3.Error as e:
            self.fail(f"SQLite error during table check: {e}")

    def test_store_and_get_cached_analysis_hit(self):
        """Test storing and then retrieving an analysis result."""
        self.analyzer._store_analysis_in_cache(self.test_fen1, self.analysis_result1)
        
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen1)
        self.assertIsNotNone(retrieved_analysis)
        self.assertEqual(retrieved_analysis, self.analysis_result1)

    def test_get_cached_analysis_miss_fen_not_present(self):
        """Test retrieving a FEN that's not in the cache."""
        retrieved_analysis = self.analyzer._get_cached_analysis("non_existent_fen_string")
        self.assertIsNone(retrieved_analysis)

    def test_store_and_get_none_analysis(self):
        """Test storing and retrieving a None analysis result."""
        self.analyzer._store_analysis_in_cache(self.test_fen2, self.analysis_result_none)
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen2)
        self.assertIsNone(retrieved_analysis) # json.loads("null") is None

    def test_get_cached_analysis_miss_depth_mismatch(self):
        """Test cache miss if analysis_depth is different."""
        self.analyzer._store_analysis_in_cache(self.test_fen1, self.analysis_result1)
        
        # Temporarily change analyzer's depth for lookup
        original_depth = self.analyzer.analysis_depth
        self.analyzer.analysis_depth = original_depth + 1 
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen1)
        self.assertIsNone(retrieved_analysis, "Should be a cache miss due to depth mismatch.")
        self.analyzer.analysis_depth = original_depth # Restore

    def test_get_cached_analysis_miss_stockfish_path_mismatch(self):
        self.analyzer._store_analysis_in_cache(self.test_fen1, self.analysis_result1)
        original_path = self.analyzer.abs_stockfish_path
        self.analyzer.abs_stockfish_path = "/different/path/to/stockfish"
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen1)
        self.assertIsNone(retrieved_analysis, "Should be a cache miss due to stockfish_path mismatch.")
        self.analyzer.abs_stockfish_path = original_path

    def test_get_cached_analysis_miss_stockfish_version_mismatch(self):
        self.analyzer._store_analysis_in_cache(self.test_fen1, self.analysis_result1)
        original_version = self.analyzer.stockfish_version_for_cache
        self.analyzer.stockfish_version_for_cache = "different_version"
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen1)
        self.assertIsNone(retrieved_analysis, "Should be a cache miss due to stockfish_version mismatch.")
        self.analyzer.stockfish_version_for_cache = original_version

    def test_store_analysis_replace(self):
        """Test that storing a new result for an existing key replaces it."""
        self.analyzer._store_analysis_in_cache(self.test_fen1, self.analysis_result1)
        new_analysis_result = {"Move": "d2d4", "Centipawn": 5, "PV": ["d2d4", "d7d5"]}
        self.analyzer._store_analysis_in_cache(self.test_fen1, new_analysis_result)
        
        retrieved_analysis = self.analyzer._get_cached_analysis(self.test_fen1)
        self.assertEqual(retrieved_analysis, new_analysis_result)

    # --- Tests for batch_analyze_positions ---
    def test_batch_analyze_positions_empty_list(self):
        """Test with an empty FEN list."""
        results = self.analyzer.batch_analyze_positions([])
        self.assertEqual(results, {})

    def test_batch_analyze_positions_normal_operation(self):
        """Test normal operation with a list of FENs."""
        fen_list = [self.test_fen1, self.test_fen2]
        mock_analysis1 = {"Move": "e2e4", "Centipawn": 10}
        mock_analysis2 = {"Move": "e7e5", "Centipawn": -10}

        def set_fen_side_effect(fen_str):
            self.mock_stockfish_instance.current_fen_for_test = fen_str
            return True 
        self.mock_stockfish_instance.set_fen_position.side_effect = set_fen_side_effect
        
        def get_top_moves_side_effect(num_moves):
            current_fen = self.mock_stockfish_instance.current_fen_for_test
            if current_fen == self.test_fen1:
                return [mock_analysis1]
            elif current_fen == self.test_fen2:
                return [mock_analysis2]
            return []
        self.mock_stockfish_instance.get_top_moves.side_effect = get_top_moves_side_effect

        # Reset mocks before the action being tested
        self.mock_stockfish_instance.set_fen_position.reset_mock()
        self.mock_stockfish_instance.get_top_moves.reset_mock()

        results = self.analyzer.batch_analyze_positions(fen_list)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[self.test_fen1], mock_analysis1)
        self.assertEqual(results[self.test_fen2], mock_analysis2)
        self.assertEqual(self.mock_stockfish_instance.set_fen_position.call_count, 2)
        self.assertEqual(self.mock_stockfish_instance.get_top_moves.call_count, 2)

    def test_batch_analyze_positions_stockfish_returns_no_moves(self):
        """Test when Stockfish get_top_moves returns an empty list or None."""
        fen_list = [self.test_fen1]
        self.mock_stockfish_instance.get_top_moves.return_value = [] # Simulate no moves found
        
        self.mock_stockfish_instance.set_fen_position.side_effect = lambda fen_str: True


        results = self.analyzer.batch_analyze_positions(fen_list)
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[self.test_fen1])

    def test_batch_analyze_positions_with_shutdown_signal(self):
        """Test batch analysis interruption by shutdown signal."""
        fen_list = [self.test_fen1, self.test_fen2, "some_other_fen3"]
        mock_analysis1 = {"Move": "e2e4", "Centipawn": 10}

        call_count = 0
        def get_top_moves_interrupt_side_effect(num_moves):
            nonlocal call_count
            call_count += 1
            current_fen = self.mock_stockfish_instance.get_fen_position()
            if current_fen == self.test_fen1:
                return [mock_analysis1]
            elif current_fen == self.test_fen2: # After this, set shutdown flag
                self.analyzer.shutdown_requested = True 
                return [] # Or some analysis
            return [] # For any subsequent calls after shutdown

        self.mock_stockfish_instance.get_top_moves.side_effect = get_top_moves_interrupt_side_effect
        self.mock_stockfish_instance.set_fen_position.side_effect = lambda fen_str: setattr(self.mock_stockfish_instance, 'current_fen_for_test', fen_str) or True
        self.mock_stockfish_instance.get_fen_position.side_effect = lambda: getattr(self.mock_stockfish_instance, 'current_fen_for_test', None)


        self.analyzer.shutdown_requested = False # Ensure it's false initially
        results = self.analyzer.batch_analyze_positions(fen_list)

        self.assertTrue(self.analyzer.shutdown_requested)
        self.assertEqual(len(results), 2) # Should have processed test_fen1 and test_fen2
        self.assertIn(self.test_fen1, results)
        self.assertIn(self.test_fen2, results)
        self.assertNotIn("some_other_fen3", results)
        self.assertEqual(call_count, 2) # get_top_moves called twice

if __name__ == '__main__':
    unittest.main()