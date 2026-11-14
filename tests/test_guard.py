"""Check that agent two cannot read the meeting notes.  Run: python tests/test_guard.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.board import read_board  # noqa: E402
from src.board_filer import load_packets  # noqa: E402
from src.guard import NotesAccessError, notes_forbidden  # noqa: E402
from src.notes_reader import DATA_DIR  # noqa: E402

NOTES = DATA_DIR / "notes"
note = sorted(NOTES.glob("*.md"))[0]

with notes_forbidden(NOTES):
    assert read_board() and load_packets(), "agent two's real inputs must still be readable"
    for attempt in (lambda: open(note), note.read_text, lambda: list(NOTES.glob("*.md"))):
        try:
            attempt()
        except NotesAccessError:
            continue
        raise SystemExit("FAIL: a read of notes/ got through the guard")

note.read_text()  # outside the guard, agent one can still read notes
print("\nPASS: inside the guard every read of notes/ raised NotesAccessError; board and handoff stayed readable")
