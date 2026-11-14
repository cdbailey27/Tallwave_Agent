"""Policy check: firm-rules.md as plain code. No model calls.

Runs on every planned change before anything is written. Where agent two and the rules
disagree, the rules win, and every override is logged with the rule that triggered it.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

from src.notes_reader import DATA_DIR
from src.schema import Item, PlannedChange

SAM = "Sam Okafor"
LENA = "Lena Fischer"
NEEDS_OWNER = "[Needs owner] "
DEFAULT_DUE = date(2026, 11, 5)
MAX_TITLE = 79

INTERNAL_TOPIC = re.compile(r"\b(budget|margin|burn|profit\w*|overrun|staffing|headcount|hiring|salar\w*)\b", re.I)
SENSITIVE_TOPIC = re.compile(r"\b(performance plan|performance review|disciplinar\w*|compensation|salary)\b", re.I)
NOT_A_VERB = {
    "a", "an", "the", "new", "task", "tasks", "meeting", "weekly", "final", "client", "project", "team",
    "cascadia", "meridian", "internal", "q1", "q2", "q3", "q4",
}


@dataclass
class Context:
    team_status: Dict[str, str]          # name -> active / inactive
    projects: Dict[str, Dict[str, str]]  # project -> row from projects.csv

    @classmethod
    def load(cls) -> "Context":
        with open(DATA_DIR / "team.csv", newline="") as f:
            team = {r["name"]: r["status"] for r in csv.DictReader(f)}
        with open(DATA_DIR / "projects.csv", newline="") as f:
            projects = {r["project"]: r for r in csv.DictReader(f)}
        return cls(team, projects)

    def active(self, name: Optional[str]) -> bool:
        return name is not None and self.team_status.get(name) == "active"

    def client_visible(self, project: Optional[str]) -> bool:
        return project in self.projects and self.projects[project]["board_visibility"] == "client-visible"

    def lead(self, project: str) -> str:
        return self.projects[project]["engagement_lead"]


@dataclass
class Outcome:
    change: PlannedChange          # the change after policy (agent two's version is in `planned`)
    planned: PlannedChange
    items: List[Item]
    packet_project: str
    overrides: List[str] = field(default_factory=list)
    review_notes: List[str] = field(default_factory=list)  # Sam's review list
    approvers: List[str] = field(default_factory=list)
    approval_reasons: List[str] = field(default_factory=list)
    sam_flag: Optional[str] = None
    lena_private: Optional[str] = None
    target_id: Optional[str] = None  # task the change writes to: T-#### or NEW-#
    before: Dict[str, str] = field(default_factory=dict)  # the target task before this change

    @property
    def is_write(self) -> bool:
        return self.change.op in ("create", "update")

    @property
    def held(self) -> bool:
        return bool(self.approvers)

    def override(self, rule: str, what: str) -> None:
        self.overrides.append(f"[{rule}] {what}")

    def need(self, person: str, why: str) -> None:
        if person not in self.approvers:
            self.approvers.append(person)
        self.approval_reasons.append(f"{person}: {why}")


# Possible-duplicate check: compare titles on content words only. Words in one group (budget,
# margin, burn...) count as the same topic, so "run budget numbers" and "send margin breakdown"
# look like the same work. Names, projects and generic verbs carry no signal and are dropped.
_STOP = {
    "a", "an", "the", "to", "for", "of", "and", "or", "on", "in", "with", "before", "after", "by", "from",
    "at", "into", "any", "all", "our", "their", "his", "her", "new", "full", "further", "agreeing", "needs",
    "owner", "run", "send", "add", "draft", "update", "set", "up", "build", "create", "make", "share",
    "confirm", "complete", "prepare", "write", "get", "review", "schedule", "mention", "export", "deliver",
    "compile", "take", "check", "finalize", "final", "weekly",
}
_TOPICS = {
    "budget": "finance", "margin": "finance", "burn": "finance", "number": "finance", "breakdown": "finance",
    "cost": "finance", "financial": "finance", "profitability": "finance", "spend": "finance",
    "spec": "spec", "specification": "spec", "comp": "comp", "mockup": "comp",
    "sow": "sow", "statement": "sow", "redirect": "redirect", "recipient": "distribution", "distribution": "distribution",
}


def _work_words(title: str, ctx: "Context") -> set:
    names = {part.lower() for name in ctx.team_status for part in name.split()} | {p.lower() for p in ctx.projects}
    words = set()
    for raw in re.findall(r"[a-z0-9]+", title.lower()):
        word = raw[:-1] if len(raw) > 3 and raw.endswith("s") else raw
        if word in _STOP or raw in _STOP or word in names or raw in names or len(word) < 3:
            continue
        words.add(_TOPICS.get(word, word))
    return words


def possible_duplicates(title: str, owner: str, project: str, board: Dict[str, Dict[str, str]], ctx: "Context") -> List[str]:
    """Open tasks with the same owner and project whose title looks like the same work."""
    mine = _work_words(title, ctx)
    found = []
    for task_id, row in board.items():
        if row.get("owner") != owner or row.get("project") != project or row.get("status") == "Done":
            continue
        theirs = _work_words(row.get("title") or "", ctx)
        shared = mine & theirs
        if mine and theirs and len(shared) / min(len(mine), len(theirs)) >= 0.5:
            found.append(task_id)
    return found


def _first_word(title: str) -> str:
    rest = title[len(NEEDS_OWNER):] if title.startswith(NEEDS_OWNER) else title
    return (rest.split() or [""])[0].strip("\"'").lower()


def _fit_title(title: str) -> str:
    if len(title) <= MAX_TITLE:
        return title
    cut = title[: MAX_TITLE - 1].rsplit(" ", 1)[0]
    return cut.rstrip(",;:-") + "…"


def check(
    planned: PlannedChange,
    items: List[Item],
    board: Dict[str, Dict[str, str]],
    packet_project: str,
    ctx: Context,
) -> Outcome:
    out = Outcome(change=planned.model_copy(deep=True), planned=planned, items=items, packet_project=packet_project)
    c = out.change
    flags = {f for item in items for f in item.flags}
    item_text = " ".join(item.summary for item in items)

    # 1. Injection: never acted on. Flag to Sam, nothing else.
    if "injection" in flags:
        if c.op != "flag_only" or c.outbound:
            out.override("sources: injected text is flagged, never acted on", f"agent two planned {c.op}; forced to flag_only")
        c.op, c.outbound = "flag_only", None
        out.sam_flag = "Text in the meeting record tried to instruct the system. Not acted on. " + c.reason
        return out

    # 2. Performance, compensation, discipline: never on any board. Lena, privately.
    if "sensitive_hr" in flags or SENSITIVE_TOPIC.search(item_text + " " + (c.title or "")):
        if c.op != "route_private" or c.outbound:
            out.override("boards: HR matters never go on any board", f"agent two planned {c.op}; forced to route_private")
        c.op, c.outbound = "route_private", None
    if c.op == "route_private":
        out.lena_private = c.reason
        return out

    if c.op == "flag_only":
        out.sam_flag = c.reason
        return out

    # 3. Board writes: owner, title, due date, priority, which board.
    if out.is_write:
        current = board.get(c.matches_task, {}) if c.op == "update" else {}

        def eff(name: str) -> Optional[str]:
            value = getattr(c, name)
            if value is not None:
                return value.isoformat() if isinstance(value, date) else value
            return current.get(name) or None

        # 3a. Internal topics never go on a client-visible board.
        if ctx.client_visible(eff("project")) and (
            "internal_only" in flags or INTERNAL_TOPIC.search(f"{eff('title')} {item_text}")
        ):
            if c.op == "update":
                out.override("boards: internal topics stay off client-visible boards",
                             f"{c.matches_task} is on a client-visible board; filing as a new Internal task instead")
                c.op, c.matches_task = "create", None
                c.title = c.title or current.get("title")
                c.due = c.due or (date.fromisoformat(current["due"]) if current.get("due") else None)
                c.priority = c.priority or current.get("priority") or None
                c.owner = c.owner or current.get("owner") or None
                current = {}
            else:
                out.override("boards: internal topics stay off client-visible boards", f"moved from {c.project} to Internal")
            c.project = "Internal"

        # 3b. Owner: active, and named by the handoff. Never guessed.
        named = {item.candidate_owner for item in items if item.kind == "action" and item.candidate_owner}
        if c.owner and c.owner != SAM and c.owner not in named and c.owner != current.get("owner"):
            out.override("tasks: do not guess owners", f"{c.owner} is not named as owner in the handoff; owner cleared")
            c.owner = None
        owner = eff("owner")
        if not ctx.active(owner):
            why = "no owner" if not owner else f"{owner} is {'inactive' if owner in ctx.team_status else 'not in team.csv'}"
            if c.owner != SAM:
                out.override("tasks: unowned work goes to Sam with [Needs owner]", f"{why}; owner set to {SAM}")
            c.owner = SAM
            owner = SAM
            needs_owner = True
        else:
            # Sam picked by agent two only because nobody was named still counts as unowned.
            needs_owner = owner == SAM and not named and current.get("owner") != SAM
        title = eff("title") or ""
        if needs_owner and not title.startswith(NEEDS_OWNER):
            title = NEEDS_OWNER + title
            out.override("tasks: unowned work goes to Sam with [Needs owner]", "added [Needs owner] to title")
        elif not needs_owner and title.startswith(NEEDS_OWNER):
            title = title[len(NEEDS_OWNER):]
            out.override("tasks: [Needs owner] only while unowned", f"owner is {owner}; removed [Needs owner] from title")
        fitted = _fit_title(title)
        if fitted != title:
            out.override("tasks: titles under 80 characters", f"shortened title to {len(fitted)} characters")
        if fitted != (current.get("title") or None) or c.op == "create":
            c.title = fitted
        if _first_word(fitted) in NOT_A_VERB:
            out.review_notes.append(f"title may not start with a verb, confirm: {fitted!r}")

        # 3c. Every task has a due date.
        if not eff("due"):
            c.due = DEFAULT_DUE
            out.override("tasks: every task has a due date", f"no due date; set default {DEFAULT_DUE}")
            out.review_notes.append(f"default due date {DEFAULT_DUE}, confirm: {fitted!r}")

        # 3d. Every task has a priority; new tasks start as To do.
        if not eff("priority"):
            c.priority = "P2"
            out.override("tasks: every task has a priority", "no priority; set P2 (the default)")
        if c.op == "create" and not c.status:
            c.status = "To do"

        # 3e. Possible duplicate: keep both, but ask Sam to confirm.
        if c.op == "create":
            for task_id in possible_duplicates(c.title, c.owner, c.project, board, ctx):
                out.review_notes.append(
                    f"possible duplicate of {task_id} ({board[task_id].get('title')!r}), confirm: {c.title!r}")

    # 4. Approvals: anything that leaves the firm, or commits the firm to cost or scope.
    project = (c.project or board.get(c.matches_task or "", {}).get("project") or packet_project)
    if project in ctx.projects:
        lead = ctx.lead(project)
        if out.is_write and ctx.client_visible(project):
            out.need(lead, f"write to the client-visible {project} board")
        if c.outbound:
            out.need(lead, "outbound message to the client")
        if out.is_write and "client_send" in flags:
            out.need(lead, "client communication about scope, dates or recipients")
        if "cost_commitment" in flags and (out.is_write or c.outbound):
            out.need(LENA, "commitment that adds cost or scope (partner approval)")
            out.need(lead, "commitment that adds cost or scope (engagement lead)")
    missed = [p for p in out.approvers if p not in planned.needs_approval_from]
    if missed:
        out.override("approvals", f"agent two did not ask {', '.join(missed)}; added")
    for extra in planned.needs_approval_from:  # agent two may ask for more sign-off, never less
        if extra not in out.approvers and (out.is_write or c.outbound):
            out.need(extra, "requested by agent two")
    c.needs_approval_from = list(out.approvers)
    return out
