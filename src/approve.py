"""Approval gate. Changes that need sign-off are held here, not written.

For each held change it shows who must approve, what will change, the evidence quote it came
from, and any draft client message, then asks each approver to approve or reject. One reject
rejects the change. Nothing is written by this module; run_pipeline writes only what passed.
"""

from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.policy import Context, Outcome


@dataclass
class Decision:
    approved: bool
    by: List[str]
    rejected_by: Optional[str] = None


def describe(outcome: Outcome, ctx: Context) -> List[str]:
    """Human-readable lines for one change, compared with the task as it was before it."""
    c = outcome.change
    lines = []
    if c.op == "create":
        vis = "client-visible" if ctx.client_visible(c.project) else "internal"
        lines.append(f"CREATE on {c.project} board ({vis})")
        lines.append(f"  {c.title}")
        lines.append(f"  owner {c.owner} | due {c.due} | {c.priority} | {c.status}")
    elif c.op == "update":
        current = outcome.before
        vis = "client-visible" if ctx.client_visible(current.get("project")) else "internal"
        lines.append(f"UPDATE {c.matches_task} on {current.get('project')} board ({vis}): {current.get('title')}")
        for name in ("title", "owner", "due", "priority", "status", "project"):
            new = getattr(c, name)
            new = new.isoformat() if hasattr(new, "isoformat") else new
            if new is not None and new != current.get(name):
                lines.append(f"  {name}: {current.get(name)} -> {new}")
    else:
        lines.append(f"{c.op.upper()} {c.matches_task or ''}".rstrip())
    return lines


def show(n: int, total: int, outcome: Outcome, ctx: Context) -> None:
    c = outcome.change
    print(f"\n--- Approval {n} of {total}: {c.change_id} " + "-" * 40)
    print(f"Needs: {', '.join(outcome.approvers)}")
    for reason in outcome.approval_reasons:
        print(f"  - {reason}")
    print("Change:")
    for line in describe(outcome, ctx):
        print(f"  {line}")
    print(f"Why: {c.reason}")
    for o in outcome.overrides:
        print(f"Policy override: {o}")
    print("Evidence:")
    for item in outcome.items:
        print(f'  {item.id}  "{item.evidence}"')
    if c.outbound:
        print("Draft message (not sent until approved; saved to outbox/sent/, no real email):")
        print(f"  To: {', '.join(c.outbound.to)}")
        print(f"  Subject: {c.outbound.subject}")
        for para in c.outbound.body.splitlines():
            print(textwrap.indent(textwrap.fill(para, 88) if para else "", "  | ") or "  |")


def ask(person: str, mode: Optional[str]) -> bool:
    if mode == "approve":
        print(f"{person}: approve or reject? [a/r] a  (auto-approve)")
        return True
    if mode == "reject":
        print(f"{person}: approve or reject? [a/r] r  (auto-reject)")
        return False
    while True:
        answer = input(f"{person}: approve or reject? [a/r] ").strip().lower()
        if answer in ("a", "approve"):
            return True
        if answer in ("r", "reject"):
            return False


def review(outcomes: List[Outcome], ctx: Context, mode: Optional[str]) -> Dict[str, Decision]:
    """Ask for sign-off on every held change. mode: None (ask), 'approve' or 'reject'."""
    held = [o for o in outcomes if o.held]
    if held and mode is None and not sys.stdin.isatty():
        raise SystemExit("Changes need approval but there is no terminal to ask. Use --auto-approve or --auto-reject.")
    print(f"\n=== Approval gate: {len(held)} change(s) held for sign-off ===")
    decisions: Dict[str, Decision] = {}
    for n, outcome in enumerate(held, start=1):
        show(n, len(held), outcome, ctx)
        decision = Decision(approved=True, by=[])
        for person in outcome.approvers:
            if ask(person, mode):
                decision.by.append(person)
            else:
                decision = Decision(approved=False, by=decision.by, rejected_by=person)
                break
        print("=> APPROVED" if decision.approved else f"=> REJECTED by {decision.rejected_by}")
        decisions[outcome.change.change_id] = decision
    return decisions
