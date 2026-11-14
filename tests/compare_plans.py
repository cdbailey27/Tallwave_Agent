"""Compare the plans from several runs on the things that matter, ignoring wording.

    python tests/compare_plans.py runs/11-dry-run-a runs/11-dry-run-b runs/11-dry-run-c

Changes are matched across runs by the action items they came from. For each, it compares op,
target task, board, owner, due date, priority and approvers, after the policy check.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.board_filer import load_packets  # noqa: E402

FIELDS = ["op", "target", "project", "owner", "due", "priority", "approvers"]


def key_rows(run_dir: Path, action_ids: set) -> dict:
    rows = {}
    for row in json.loads((run_dir / "plan.json").read_text()):
        change = row["after_policy"]
        sources = change["source_item_ids"]
        key = tuple(sorted(i for i in sources if i in action_ids)) or tuple(sorted(sources))
        target = row["target_id"] or ""
        rows[key] = {
            "op": change["op"],
            "target": "" if target.startswith("NEW-") else target,  # new-task placeholders vary
            "project": change["project"] or "",
            "owner": change["owner"] or "",
            "due": change["due"] or "",
            "priority": change["priority"] or "",
            "approvers": ", ".join(sorted(row["approvers"])),
        }
    return rows


def main(run_dirs):
    action_ids = {i.id for p in load_packets() for i in p.items if i.kind == "action"}
    runs = {d.name: key_rows(d, action_ids) for d in run_dirs}
    keys = sorted(set().union(*runs.values()))
    differences = 0
    for key in keys:
        versions = {name: rows.get(key) for name, rows in runs.items()}
        if any(v is None for v in versions.values()):
            differences += 1
            present = [n for n, v in versions.items() if v]
            print(f"{', '.join(key)}: only planned in {present}")
            continue
        for field in FIELDS:
            values = {name: v[field] for name, v in versions.items()}
            if field not in ("op", "target") and versions[next(iter(versions))]["op"] == "no_change":
                continue  # a no_change writes nothing; only which task it matched matters
            if len(set(values.values())) > 1:
                differences += 1
                print(f"{', '.join(key)}  {field}: " + " | ".join(f"{n}={val or '-'}" for n, val in values.items()))
    print(f"\n{len(keys)} changes compared across {len(runs)} runs. Differences: {differences}")


if __name__ == "__main__":
    main([ROOT / arg for arg in sys.argv[1:]])
