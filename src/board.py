"""The board writer. Plain functions over a CSV file, the firm's "live" task board.

There is deliberately no delete function and no email function: nothing in this pipeline can
remove work from the board or send anything outside the firm.

The live board is board/board.csv, seeded from the mock data pack the first time (or with
reset_board()). The data pack's own board.csv is never modified.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from src.notes_reader import DATA_DIR, ROOT

BOARD_PATH = ROOT / "board" / "board.csv"
SEED_PATH = DATA_DIR / "board.csv"
COLUMNS = ["id", "title", "project", "owner", "status", "due", "priority", "created"]
EDITABLE = {"title", "project", "owner", "status", "due", "priority"}
TODAY = "2026-10-29"


def reset_board() -> None:
    """Start the live board again from the data pack snapshot."""
    BOARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SEED_PATH, BOARD_PATH)


def read_board(path: Optional[Path] = None) -> List[Dict[str, str]]:
    """The live board (or another board file, e.g. the data pack's seed for a dry run)."""
    if path is None and not BOARD_PATH.exists():
        reset_board()
    with open(path or BOARD_PATH, newline="") as f:
        return list(csv.DictReader(f))


def _write_board(rows: List[Dict[str, str]]) -> None:
    with open(BOARD_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def _before_write(run_dir: Path) -> None:
    """Snapshot the board before the first write of a run."""
    run_dir.mkdir(parents=True, exist_ok=True)
    snapshot = run_dir / "board.before.csv"
    if not snapshot.exists():
        read_board()  # seeds the live board if needed
        shutil.copyfile(BOARD_PATH, snapshot)


def _log(run_dir: Path, entry: dict) -> None:
    entry = {"at": datetime.now().isoformat(timespec="seconds"), **entry}
    with open(run_dir / "changes.log", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _next_id(rows: List[Dict[str, str]]) -> str:
    highest = max(int(r["id"].split("-")[1]) for r in rows if r["id"].startswith("T-"))
    return f"T-{highest + 1}"


def create_task(
    run_dir: Path,
    *,
    title: str,
    project: str,
    owner: str,
    due: str,
    priority: str,
    status: str = "To do",
    source: Optional[dict] = None,
) -> str:
    """Add a task. Returns its new id, which continues from the highest existing T- number."""
    _before_write(run_dir)
    rows = read_board()
    task = {
        "id": _next_id(rows), "title": title, "project": project, "owner": owner,
        "status": status, "due": due, "priority": priority, "created": TODAY,
    }
    rows.append(task)
    _write_board(rows)
    _log(run_dir, {"op": "create", "task_id": task["id"], "task": task, **(source or {})})
    return task["id"]


def update_task(run_dir: Path, task_id: str, *, source: Optional[dict] = None, **fields: str) -> Dict[str, str]:
    """Change some fields of an existing task. Returns the task after the change."""
    bad = set(fields) - EDITABLE
    if bad:
        raise ValueError(f"cannot update fields {sorted(bad)}")
    _before_write(run_dir)
    rows = read_board()
    for row in rows:
        if row["id"] == task_id:
            before = {k: row[k] for k in fields}
            row.update({k: v for k, v in fields.items()})
            _write_board(rows)
            _log(run_dir, {"op": "update", "task_id": task_id, "before": before, "after": fields, **(source or {})})
            return row
    raise KeyError(f"task {task_id} is not on the board")
