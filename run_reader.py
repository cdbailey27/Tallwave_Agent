"""Run agent one over every meeting note, in date order, and print a summary per note."""

from __future__ import annotations

import logging
from collections import Counter

import anthropic
from dotenv import load_dotenv

load_dotenv()

from src.notes_reader import DATA_DIR, FAILED_DIR, HANDOFF_DIR, read_note  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    client = anthropic.Anthropic()

    # File names start with YYYY-MM-DD, so a name sort is a date sort.
    notes = sorted((DATA_DIR / "notes").glob("*.md"))
    print(f"Reading {len(notes)} notes from {DATA_DIR / 'notes'}\n")

    failures = 0
    for note_path in notes:
        packet = read_note(note_path, client)
        if packet is None:
            failures += 1
            print(f"FAILED  {note_path.name}  (see {FAILED_DIR.relative_to(HANDOFF_DIR.parent)}/)\n")
            continue

        kinds = Counter(item.kind for item in packet.items)
        flags = Counter(flag for item in packet.items for flag in item.flags)
        print(f"OK      {note_path.name}  project={packet.project}  items={len(packet.items)}")
        print("        kinds: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
        print("        flags: " + (", ".join(f"{f}={n}" for f, n in sorted(flags.items())) or "none"))
        print()

    print(f"Done. {len(notes) - failures} packets written to {HANDOFF_DIR}, {failures} failed.")


if __name__ == "__main__":
    main()
