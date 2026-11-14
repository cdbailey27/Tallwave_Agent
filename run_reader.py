"""Run agent one over every meeting note, in date order, and print a summary per note.

    python run_reader.py                         normal run
    python run_reader.py --break meridian        corrupt the first reply for one note: shows the retry
    python run_reader.py --break-twice meridian  corrupt the retry too: shows the failure path

The note is matched by any part of its file name.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter

import anthropic
from dotenv import load_dotenv

load_dotenv()

from src.notes_reader import DATA_DIR, FAILED_DIR, HANDOFF_DIR, ROOT, read_note  # noqa: E402

PATH_LABELS = {
    "first_try": "OK, passed validation on the first try",
    "retried": "RECOVERED, failed validation, passed on retry",
    "failed": "FAILED, failed validation twice, raw output saved, run continues",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--break", dest="break_once", metavar="NOTE", help="corrupt the first Claude reply for NOTE")
    group.add_argument("--break-twice", metavar="NOTE", help="corrupt the first reply and the retry for NOTE")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    client = anthropic.Anthropic()

    # File names start with YYYY-MM-DD, so a name sort is a date sort.
    notes = sorted((DATA_DIR / "notes").glob("*.md"))
    target = args.break_once or args.break_twice
    if target and not any(target in n.name for n in notes):
        parser.error(f"no note matches {target!r}")
    print(f"Reading {len(notes)} notes from {DATA_DIR.relative_to(ROOT)}/notes")
    if target:
        mode = "--break-twice" if args.break_twice else "--break"
        print(f"Test mode {mode}: corrupting Claude's output for the note matching {target!r}")
    print()

    counts = Counter()
    for note_path in notes:
        corrupt_attempts = 0
        if target and target in note_path.name:
            corrupt_attempts = 2 if args.break_twice else 1

        print(f"> {note_path.name}")
        result = read_note(note_path, client, corrupt_attempts=corrupt_attempts)
        counts[result.path] += 1
        print(f"  PATH: {PATH_LABELS[result.path]}")

        if result.packet is None:
            print(f"  wrote {FAILED_DIR.relative_to(ROOT)}/{note_path.stem}.raw.txt and .error.txt")
            print()
            continue

        packet = result.packet
        kinds = Counter(item.kind for item in packet.items)
        flags = Counter(flag for item in packet.items for flag in item.flags)
        print(f"  project={packet.project}  items={len(packet.items)}")
        print("  kinds: " + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items())))
        print("  flags: " + (", ".join(f"{f}={n}" for f, n in sorted(flags.items())) or "none"))
        print()

    print(
        f"Done. {counts['first_try']} first try, {counts['retried']} recovered on retry, "
        f"{counts['failed']} failed. Packets in {HANDOFF_DIR.relative_to(ROOT)}/"
    )


if __name__ == "__main__":
    main()
