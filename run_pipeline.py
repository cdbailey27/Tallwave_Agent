"""Run the whole pipeline: reader -> filer -> policy -> approval -> write.

    python run_pipeline.py --dry-run                plan only: no board writes, no outbox
    python run_pipeline.py --auto-approve           approve every held change without typing
    python run_pipeline.py --auto-reject            reject every held change without typing
    python run_pipeline.py                          ask each approver in the terminal
    python run_pipeline.py --break meridian         corrupt agent one's first reply for a note
    python run_pipeline.py --break-twice meridian   ...and its retry (that note drops out)

Other options: --skip-reader (reuse handoff/), --reset-board (start from the data pack's board),
--run-name NAME (folder under runs/).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import anthropic
from dotenv import load_dotenv

load_dotenv()

from src import board as board_writer  # noqa: E402
from src.approve import Decision, describe, review  # noqa: E402
from src.board_filer import load_packets, plan_packet  # noqa: E402
from src.guard import notes_forbidden  # noqa: E402
from src.notes_reader import DATA_DIR, ROOT, read_note  # noqa: E402
from src.policy import Context, Outcome, check  # noqa: E402
from src.schema import PlannedChange  # noqa: E402

OUTBOX = ROOT / "outbox"


def run_reader(client: anthropic.Anthropic, args: argparse.Namespace) -> None:
    target = args.break_once or args.break_twice
    print("=== Step 1: agent one reads the notes ===")
    for note_path in sorted((DATA_DIR / "notes").glob("*.md")):
        corrupt = (2 if args.break_twice else 1) if target and target in note_path.name else 0
        result = read_note(note_path, client, corrupt_attempts=corrupt)
        items = len(result.packet.items) if result.packet else 0
        print(f"  {note_path.name}: {result.path}" + (f", {items} items" if result.packet else ""))


def apply_to_working(board: Dict[str, Dict[str, str]], outcome: Outcome, new_ids: List[int]) -> None:
    """Record a planned change on the in-memory board so later meetings see it."""
    c = outcome.change
    if c.op == "create":
        new_ids[0] += 1
        task_id = f"NEW-{new_ids[0]}"
        board[task_id] = {
            "id": task_id, "title": c.title, "project": c.project, "owner": c.owner, "status": c.status,
            "due": c.due.isoformat() if c.due else "", "priority": c.priority, "created": board_writer.TODAY,
        }
        outcome.target_id = task_id
    elif c.op in ("update", "no_change"):
        outcome.target_id = c.matches_task
        outcome.before = dict(board[c.matches_task])
        if c.op == "update":
            for name in ("title", "project", "owner", "status", "due", "priority"):
                value = getattr(c, name)
                if value is not None:
                    board[c.matches_task][name] = value.isoformat() if hasattr(value, "isoformat") else value


def plan(client: anthropic.Anthropic, run_dir: Path, ctx: Context, board_path: Optional[Path]) -> List[Outcome]:
    print("\n=== Step 2: agent two plans board changes (notes/ is locked) ===")
    working = {row["id"]: dict(row) for row in board_writer.read_board(board_path)}
    team, projects = set(ctx.team_status), set(ctx.projects)
    outcomes: List[Outcome] = []
    new_ids = [0]
    for packet in load_packets():
        filer_plan = plan_packet(client, packet, working, team, projects, run_dir)
        items = {item.id: item for item in packet.items}
        changes = list(filer_plan.changes) if filer_plan else []
        covered = {i for change in changes for i in change.source_item_ids}
        # Injection and HR items are policy's business even when they aren't actions.
        for item in packet.items:
            if item.id not in covered and {"injection", "sensitive_hr"} & set(item.flags):
                op = "flag_only" if "injection" in item.flags else "route_private"
                changes.append(PlannedChange(source_item_ids=[item.id], op=op, reason=f"{item.summary}. {item.notes}"))
        status = "planned" if filer_plan else "FAILED (see failed/)"
        logged = sum(1 for i in packet.items if i.kind != "action")
        print(f"  {packet.meeting_file}: {status}, {len(changes)} changes; {logged} decision/info/noise items logged only")
        for change in changes:
            change.change_id = f"C{len(outcomes) + 1:02d}"
            outcome = check(change, [items[i] for i in change.source_item_ids], working, packet.project, ctx)
            apply_to_working(working, outcome, new_ids)
            outcomes.append(outcome)
    return outcomes


def print_plan(outcomes: List[Outcome], ctx: Context) -> None:
    print("\n=== Step 3: plan after policy check ===")
    for o in outcomes:
        c = o.change
        target = o.target_id or ""
        gate = f"  HOLD for {', '.join(o.approvers)}" if o.held else ""
        print(f"\n{c.change_id} {c.op.upper():<13} {target:<7} from {', '.join(c.source_item_ids)}{gate}")
        if o.is_write:
            lines = describe(o, ctx)
            if c.op == "create":
                print(f"    on {lines[0].split(' on ', 1)[1]}")
            for line in lines[1:]:
                print(f"    {line.strip()}")
        print(f"    why: {c.reason}")
        if c.outbound:
            print(f"    draft to {', '.join(c.outbound.to)}: {c.outbound.subject}")
        for override in o.overrides:
            print(f"    POLICY {override}")
        for note in o.review_notes:
            print(f"    SAM REVIEW {note}")


def save_plan(outcomes: List[Outcome], run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    rows = [{
        "change_id": o.change.change_id, "target_id": o.target_id,
        "planned_by_agent_two": o.planned.model_dump(mode="json"),
        "after_policy": o.change.model_dump(mode="json"),
        "overrides": o.overrides, "approvers": o.approvers, "approval_reasons": o.approval_reasons,
        "sam_review": o.review_notes, "sam_flag": o.sam_flag, "lena_private": o.lena_private,
        "evidence": {i.id: i.evidence for i in o.items},
    } for o in outcomes]
    (run_dir / "plan.json").write_text(json.dumps(rows, indent=2))
    with open(run_dir / "policy_overrides.log", "w") as f:
        for o in outcomes:
            for override in o.overrides:
                f.write(f"{o.change.change_id} {override}\n")


def note_file(folder: str, run: str, name: str, text: str) -> Path:
    path = OUTBOX / folder / f"{run}__{name.replace('#', '-')}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def follow_up_note(o: Outcome, decision: Decision, ctx: Context, run: str) -> Optional[Path]:
    """A rejected change tied to something the client already heard: tell the engagement lead."""
    flags = {f for item in o.items for f in item.flags}
    if not flags & {"client_send", "cost_commitment"}:
        return None
    c = o.change
    project = c.project or o.before.get("project") or o.packet_project
    lead = ctx.lead(project)
    said = "\n".join(f'> {i.id}: "{i.evidence}"' for i in o.items)
    if "cost_commitment" in flags:
        opening = (f"The commitment you made to the client wasn't approved by {decision.rejected_by}, so it needs a "
                   "follow-up with the client. Nothing was written to the board and no message went out.")
    else:
        opening = (f"{decision.rejected_by} rejected a change tied to something the client already heard, so the client "
                   "may still be expecting it. Nothing was written to the board and no message went out. Please follow up.")
    body = (f"# For {lead} (engagement lead, {project})\n\n{opening}\n\n"
            f"What was said to the client:\n{said}\n\nRejected change ({c.change_id}):\n"
            + "\n".join(f"    {line}" for line in describe(o, ctx)) + "\n"
            + (f"\nDraft that was NOT sent: \"{c.outbound.subject}\" to {', '.join(c.outbound.to)}\n" if c.outbound else ""))
    return note_file(lead.split()[0].lower(), run, c.change_id, body)


def execute(outcomes: List[Outcome], decisions: Dict[str, Decision], run_dir: Path, run: str, ctx: Context) -> Counter:
    """Write everything that passed policy and approval, in plan order."""
    print("\n=== Step 5: write ===")
    counts: Counter = Counter()
    real_ids: Dict[str, str] = {}
    rejected = []
    for o in outcomes:
        c = o.change
        sources = ", ".join(c.source_item_ids)
        if c.op == "flag_only":
            path = note_file("sam-flags", run, c.source_item_ids[0],
                             f"# For Sam Okafor\n\n{o.sam_flag}\n\nSource: {sources}\n" +
                             "".join(f'\n> {i.id}: "{i.evidence}"' for i in o.items) + "\n")
            print(f"  {c.change_id} flagged to Sam -> {path.relative_to(ROOT)}")
            counts["flagged to Sam"] += 1
            continue
        if c.op == "route_private":
            path = note_file("lena-private", run, c.source_item_ids[0],
                             f"# Private, for Lena Fischer only\n\n{o.lena_private}\n\nSource: {sources}\n"
                             "This was kept off every board.\n")
            print(f"  {c.change_id} routed privately to Lena -> {path.relative_to(ROOT)}")
            counts["routed privately to Lena"] += 1
            continue
        decision = decisions.get(c.change_id)
        if o.held and not (decision and decision.approved):
            rejected.append({"change_id": c.change_id, "rejected_by": decision.rejected_by if decision else None,
                             "change": c.model_dump(mode="json"), "evidence": {i.id: i.evidence for i in o.items}})
            print(f"  {c.change_id} rejected by {decision.rejected_by if decision else '?'}, not written, no draft sent")
            note = follow_up_note(o, decision, ctx, run) if decision else None
            if note:
                print(f"  {c.change_id} follow-up for the engagement lead -> {note.relative_to(ROOT)}")
                counts["follow-ups to engagement lead"] += 1
            continue
        source = {"change_id": c.change_id, "source_item_ids": c.source_item_ids,
                  "approved_by": decision.by if decision else []}
        if c.op == "create":
            task_id = board_writer.create_task(
                run_dir, title=c.title, project=c.project, owner=c.owner, due=c.due.isoformat(),
                priority=c.priority, status=c.status, source=source)
            real_ids[o.target_id] = task_id
            print(f"  {c.change_id} created {task_id}: {c.title}")
            counts["created"] += 1
        elif c.op == "update":
            target = real_ids.get(o.target_id, o.target_id)
            if target.startswith("NEW-"):
                print(f"  {c.change_id} skipped: it updates {o.target_id}, which was never created")
                counts["skipped"] += 1
                continue
            fields = {k: (v.isoformat() if hasattr(v, "isoformat") else v)
                      for k in ("title", "project", "owner", "status", "due", "priority")
                      if (v := getattr(c, k)) is not None}
            board_writer.update_task(run_dir, target, source=source, **fields)
            print(f"  {c.change_id} updated {target}: {', '.join(f'{k}={v}' for k, v in fields.items())}")
            counts["updated"] += 1
        else:
            counts["no change"] += 1
        if c.outbound:
            path = note_file("sent", run, c.change_id,
                             f"To: {', '.join(c.outbound.to)}\nSubject: {c.outbound.subject}\n"
                             f"Approved by: {', '.join(decision.by)}\n\n{c.outbound.body}\n")
            print(f"  {c.change_id} draft saved (no real email) -> {path.relative_to(ROOT)}")
            counts["drafts saved"] += 1
    (run_dir / "rejected.json").write_text(json.dumps(rejected, indent=2))
    def real(text: str) -> str:  # NEW-# ids were placeholders while planning; show the real task id
        return re.sub(r"NEW-\d+", lambda m: real_ids.get(m.group(0), f"{m.group(0)} (not created)"), text)

    review_notes = [f"- {o.change.change_id}: {real(note)}" for o in outcomes for note in o.review_notes]
    if review_notes:
        text = "# Sam Okafor: review list\n\n" + "\n".join(review_notes) + "\n"
        note_file("sam-flags", run, "review-list", text)
        (run_dir / "sam_review.md").write_text(text)
    return counts


def print_board_diff(run_dir: Path) -> None:
    before_path = run_dir / "board.before.csv"
    after = {r["id"]: r for r in board_writer.read_board()}
    shutil.copyfile(board_writer.BOARD_PATH, run_dir / "board.after.csv")
    before = {}
    if before_path.exists():
        with open(before_path, newline="") as f:
            before = {r["id"]: r for r in csv.DictReader(f)}
    print("\n=== Board after this run (* new, ~ changed) ===")
    print(f"   {'id':<7} {'project':<9} {'owner':<15} {'status':<11} {'due':<10} pri title")
    for task_id, row in after.items():
        mark = "*" if task_id not in before else ("~" if row != before[task_id] else " ")
        print(f" {mark} {task_id:<7} {row['project']:<9} {row['owner']:<15} {row['status']:<11} {row['due']:<10} "
              f"{row['priority']:<3} {row['title']}")
        if mark == "~":
            for k in row:
                if row[k] != before[task_id][k]:
                    print(f"{'':>12}{k}: {before[task_id][k]} -> {row[k]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="plan only; write nothing to the board or outbox")
    breaks = parser.add_mutually_exclusive_group()
    breaks.add_argument("--break", dest="break_once", metavar="NOTE", help="corrupt agent one's first reply for NOTE")
    breaks.add_argument("--break-twice", metavar="NOTE", help="corrupt agent one's first reply and retry for NOTE")
    gate = parser.add_mutually_exclusive_group()
    gate.add_argument("--auto-approve", action="store_true", help="approve every held change")
    gate.add_argument("--auto-reject", action="store_true", help="reject every held change")
    parser.add_argument("--skip-reader", action="store_true", help="reuse the packets already in handoff/")
    parser.add_argument("--reset-board", action="store_true", help="start from the data pack's board.csv")
    parser.add_argument("--run-name", default=datetime.now().strftime("pipeline-%Y%m%d-%H%M%S"))
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(name)s: %(message)s", stream=sys.stdout)
    run_dir = ROOT / "runs" / args.run_name
    if run_dir.exists():
        parser.error(f"{run_dir.relative_to(ROOT)} already exists; pick another --run-name")
    if args.reset_board and not args.dry_run:
        board_writer.reset_board()
    client = anthropic.Anthropic()
    print(f"Run: runs/{args.run_name}{'  (DRY RUN: nothing will be written)' if args.dry_run else ''}\n")

    if not args.skip_reader:
        run_reader(client, args)

    # From here on, nothing may read the meeting notes. Agent two works from handoff/ only.
    with notes_forbidden(DATA_DIR / "notes"):
        ctx = Context.load()
        # A dry run with --reset-board plans against the data pack's board without touching the live one.
        board_path = board_writer.SEED_PATH if (args.dry_run and args.reset_board) else None
        outcomes = plan(client, run_dir, ctx, board_path)
        save_plan(outcomes, run_dir)
        print_plan(outcomes, ctx)

        ops = Counter(o.change.op for o in outcomes)
        held = [o for o in outcomes if o.held]
        overrides = sum(len(o.overrides) for o in outcomes)
        if args.dry_run:
            print("\n=== DRY RUN summary (nothing written) ===")
            print(f"  planned: create {ops['create']}, update {ops['update']}, no change {ops['no_change']}, "
                  f"flag to Sam {ops['flag_only']}, route to Lena {ops['route_private']}")
            print(f"  would be held for approval: {len(held)}")
            print(f"  Sam review items: {sum(len(o.review_notes) for o in outcomes)}")
            print(f"  policy overrides: {overrides}  (see runs/{args.run_name}/policy_overrides.log)")
            return

        mode = "approve" if args.auto_approve else "reject" if args.auto_reject else None
        print("\n=== Step 4: approval gate ===", end="")
        decisions = review(outcomes, ctx, mode)
        counts = execute(outcomes, decisions, run_dir, args.run_name, ctx)
        print_board_diff(run_dir)

    approved = sum(1 for d in decisions.values() if d.approved)
    summary = [
        f"created:                  {counts['created']}",
        f"updated:                  {counts['updated']}",
        f"no change:                {counts['no change']}",
        f"held for approval:        {len(held)}",
        f"  approved:               {approved}",
        f"  rejected:               {len(held) - approved}",
        f"flagged to Sam:           {counts['flagged to Sam']}  (+ {sum(len(o.review_notes) for o in outcomes)} on his review list)",
        f"routed privately to Lena: {counts['routed privately to Lena']}",
        f"follow-ups to eng. lead:  {counts['follow-ups to engagement lead']}  (rejected, but the client already heard it)",
        f"policy overrides:         {overrides}",
        f"client drafts saved:      {counts['drafts saved']}  (outbox/sent/, no real email)",
    ]
    if counts["skipped"]:
        summary.append(f"skipped (depends on a rejected change): {counts['skipped']}")
    print(f"\n=== Summary: runs/{args.run_name} ===")
    print("\n".join(f"  {line}" for line in summary))
    (run_dir / "summary.txt").write_text("\n".join(summary) + "\n")


if __name__ == "__main__":
    main()
