# Chess Game Analyzer (chess_analyzerv2)

A Python script to perform in-depth analysis of chess games from PGN files using the Stockfish chess engine. It annotates games with evaluations, centipawn loss (CPL), move classifications (Blunder, Mistake, Inaccuracy), missed mates, and provides Lichess-style Win Chance percentages.

## Features

*   **Detailed Move-by-Move Analysis**: Leverages the Stockfish chess engine for comprehensive evaluation of each move.
*   **Lichess-Style Classifications**:
    *   Identifies Blunders (including Missed Mates), Mistakes, and Inaccuracies.
    *   Classifications are based on both Centipawn Loss (CPL) and Win Chance percentage changes.
    *   Leniency is applied in decisively winning positions.
*   **Average Centipawn Loss (ACPL)**: Calculates ACPL for both White and Black for each game.
*   **Informative Annotations**:
    *   Adds `[%eval]` tags with engine scores (centipawns or mate scores) and analysis depth.
    *   Includes classification comments detailing the type of error, CPL/WC impact, the engine's suggested best move (SAN), and the Principal Variation (PV).
*   **Persistent Caching**: Uses an SQLite database (`chess_analyzer_cache.db`) to store FEN analysis results. This significantly speeds up re-analysis of previously seen positions across multiple games or script runs. Cache validity is tied to Stockfish path, version, and analysis depth.
*   **Graceful Shutdown**: Handles `Ctrl+C` (SIGINT) and `SIGTERM` signals to attempt a clean shutdown, ensuring data (like the current state of the cache) is saved.
*   **Player-Specific Focus**: Optionally, classification comments in the output PGN can be focused on a specific player's moves.
*   **Customizable Output**:
    *   Adjustable PGN output column width for move text wrapping.
    *   Analyzed games are appended to the output PGN file, preserving existing data.
*   **Skipping Processed Games**: Avoids re-analyzing games already present in the output PGN file by checking Game IDs.

## Prerequisites

*   **Python**: Version 3.8 or higher is recommended.
*   **Stockfish Chess Engine**:
    *   Download the latest version or a specific version you prefer from the [official Stockfish website](https://stockfishchess.org/download/).
    *   Ensure the Stockfish executable is placed in a known location on your system and is executable.

## Setup

1.  **Clone the Repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/chess-analyzer-project.git 
    cd chess-analyzer-project
    ```
    (Replace `YOUR_USERNAME/chess-analyzer-project` with your actual repository URL)

2.  **Create and Activate a Virtual Environment (Recommended):**
    ```bash
    python3 -m venv venv
    source venv/bin/activate  # On Linux/macOS
    # venv\Scripts\activate   # On Windows
    ```

3.  **Install Dependencies:**
    The script relies on `python-chess` and `python-stockfish`. These can be installed from the `requirements.txt` file (if you create one) or directly:
    ```bash
    pip install python-chess python-stockfish
    ```
    (If you have a `requirements.txt` file, use `pip install -r requirements.txt`)

4.  **Ensure Stockfish is Ready:**
    Make a note of the full path to your Stockfish executable. You will need this for the `--stockfish_path` argument.

## Usage

The script is run from the command line.

**Basic Command Structure:**

```bash
python3 chess_analyzerv2.py <input_pgn_file> <output_pgn_file> --stockfish_path /path/to/your/stockfish [OPTIONS]

Required Arguments:

    input_pgn_file: Path to the PGN file containing the games you want to analyze.

    output_pgn_file: Path to the PGN file where annotated games will be saved. If the file exists, new games will be appended.

    --stockfish_path </path/to/stockfish>: Full path to the Stockfish executable.

Optional Arguments:

    --player_name "PLAYER_NAME": If specified, classification comments (Blunder, Mistake, Inaccuracy) will only be added to the PGN for moves made by this player (case-insensitive). The console output will also highlight this player's ACPL. If not provided, all moves for both players are candidates for classification comments.

    --depth DEPTH: The analysis depth Stockfish will use for each position (default: 18). Higher depths are stronger but significantly slower.

    --threads THREADS: The number of CPU threads Stockfish can use (default: half of your system's logical cores).

    --hash HASH_MB: The amount of memory (in MB) Stockfish can use for its hash table (default: 128).

    --cpl_inaccuracy CPL_VALUE: Centipawn loss threshold for a move to be considered an "Inaccuracy (CPL)" if not classified by Win Chance (default: 100).

    --cpl_mistake CPL_VALUE: Centipawn loss threshold for a move to be considered a "Mistake (CPL)" (default: 250).

    --cpl_blunder CPL_VALUE: Centipawn loss threshold for a move to be considered a "Blunder (CPL)" (default: 400).

    --pgn_columns WIDTH: The desired column width for wrapping move text in the output PGN (default: 80).

Example:
        python3 chess_analyzerv2.py "my_raw_games.pgn" "analyzed_games.pgn" --stockfish_path "/opt/stockfish/stockfish" --depth 20 --player_name "MyChessUsername" --threads 4

        This command will:

    Read games from my_raw_games.pgn.

    Analyze them using Stockfish located at /opt/stockfish/stockfish at depth 20 with 4 threads.

    Write/append annotated games to analyzed_games.pgn.

    Only add detailed classification comments (Blunder, Mistake, Inaccuracy) in the PGN for moves made by "MyChessUsername".

Running Tests

The project includes unit and integration tests to ensure functionality. To run the tests:

    Navigate to the project's root directory.

    Ensure your virtual environment is activated.

    Run the unittest discovery command:

        python3 -m unittest discover tests

        For more detailed output:

        python3 -m unittest -v discover tests

Cache System

    The script utilizes an SQLite database named chess_analyzer_cache.db (created in the script's working directory) to store FEN analysis results.

    This cache is specific to the combination of:

        Stockfish executable path (absolute).

        Stockfish engine version.

        Analysis depth.

    If you change any of these parameters, the script will effectively use a different "section" of the cache or a new cache space, ensuring that results from different settings do not conflict.

    The cache significantly speeds up processing when analyzing large PGN files or re-analyzing games, as previously computed FEN evaluations are retrieved quickly.

License

This project is licensed under the terms of the GNU General Public License v3.0 or later.
See the LICENSE file for the full license text.
Contributing

Contributions, bug reports, and feature requests are welcome! Please feel free to open an issue or submit a pull request on the GitHub repository.

