"""Start the live board (board/board.csv) again from the data pack's board.csv."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.board import BOARD_PATH, read_board, reset_board  # noqa: E402

reset_board()
print(f"Board reset: {len(read_board())} tasks in {BOARD_PATH.relative_to(BOARD_PATH.parent.parent)}")
