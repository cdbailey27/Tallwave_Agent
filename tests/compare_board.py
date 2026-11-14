"""Compare a run's board with tests/expected_board.md.  Run: python tests/compare_board.py runs/<run>"""

import csv
import difflib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIELDS = ["title", "project", "owner", "status", "due", "priority"]


def expected_tables():
    """Parse the two markdown tables (existing tasks, new tasks) from expected_board.md."""
    text = (ROOT / "tests" / "expected_board.md").read_text()
    rows = []
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 7 and (cells[0].startswith("T-") or cells[0] == "new"):
            rows.append(dict(zip(["id"] + FIELDS, cells)))
    return [r for r in rows if r["id"] != "new"], [r for r in rows if r["id"] == "new"]


def ok(expected: str, actual: str, field: str) -> bool:
    if expected == "*":
        return True
    if field == "title":  # titles: close enough in meaning is fine; check loosely
        return difflib.SequenceMatcher(None, expected.lower(), actual.lower()).ratio() > 0.45
    return actual in expected.split("/")


def main(run_dir: Path) -> None:
    with open(run_dir / "board.after.csv", newline="") as f:
        actual = {r["id"]: r for r in csv.DictReader(f)}
    with open(run_dir / "board.before.csv", newline="") as f:
        before_ids = {r["id"] for r in csv.DictReader(f)}
    existing, new = expected_tables()
    problems = 0

    print("Existing tasks:")
    for exp in existing:
        row = actual.get(exp["id"])
        bad = [f"{k}: expected {exp[k]!r}, got {row[k]!r}" for k in FIELDS if row and not ok(exp[k], row[k], k)]
        if not row:
            bad = ["missing from board"]
        problems += len(bad)
        print(f"  {'OK  ' if not bad else 'DIFF'} {exp['id']} {exp['title'] if exp['title'] != '*' else ''}")
        for b in bad:
            print(f"         {b}")

    print("\nNew tasks:")
    created = [r for i, r in actual.items() if i not in before_ids]
    unmatched = list(created)
    for exp in new:
        candidates = [r for r in unmatched if r["project"] == exp["project"]]
        best = max(candidates, key=lambda r: difflib.SequenceMatcher(None, exp["title"].lower(), r["title"].lower()).ratio(),
                   default=None)
        if best is None or difflib.SequenceMatcher(None, exp["title"].lower(), best["title"].lower()).ratio() < 0.35:
            problems += 1
            print(f"  MISSING  {exp['project']}: {exp['title']}")
            continue
        unmatched.remove(best)
        bad = [f"{k}: expected {exp[k]!r}, got {best[k]!r}" for k in FIELDS[1:] if not ok(exp[k], best[k], k)]
        problems += len(bad)
        print(f"  {'OK  ' if not bad else 'DIFF'}     {best['id']} {best['title']}")
        for b in bad:
            print(f"           {b}")
    for row in unmatched:
        problems += 1
        print(f"  EXTRA    {row['id']} {row['project']}: {row['title']} ({row['owner']}, {row['due']})")

    print(f"\n{len(actual)} tasks on board (expected {len(existing) + len(new)}). Differences: {problems}")


if __name__ == "__main__":
    main(ROOT / sys.argv[1])
