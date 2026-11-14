"""Handoff packet schema: the contract between agent one (notes reader) and agent two (board filer).

One packet per meeting note. Agent two reads packets, never the raw notes.

Some checks need outside data (the team directory, the project list, the note text).
Pass those in through Pydantic's validation context:

    HandoffPacket.model_validate_json(raw, context={
        "team_names": {...}, "projects": {...}, "note_text": "...",
    })

Without a context, only the shape is checked.
"""

from __future__ import annotations

import re
from datetime import date
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

Kind = Literal["action", "decision", "info", "noise"]
Confidence = Literal["high", "medium", "low"]
Flag = Literal[
    "ambiguous_owner",  # owner unclear or could be more than one person
    "no_date",          # no due date was given
    "inactive_owner",   # the named owner is inactive in team.csv
    "internal_only",    # budget, margin, staffing: must not reach a client-visible board
    "client_send",      # something is sent or stated to the client
    "cost_commitment",  # adds cost or scope for the firm (free work, extra rounds)
    "sensitive_hr",     # performance, compensation, discipline: never on any board
    "tentative",        # "maybe", "probably", "if he has room"
    "injection",        # text in the note that tries to instruct the system
]


def _squash(text: str) -> str:
    """Normalize whitespace and quote styles so evidence matching isn't brittle."""
    text = text.replace("“", '"').replace("”", '"').replace("’", "'").replace("‘", "'")
    return re.sub(r"\s+", " ", text).strip().lower()


class Item(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default="", description="Set by the system after validation. Leave empty.")
    kind: Kind
    summary: str = Field(min_length=3, max_length=79, description="Verb-first, under 80 characters")
    candidate_owner: Optional[str] = Field(description="Full name from team.csv, or null if unclear")
    due: Optional[date] = Field(description="YYYY-MM-DD resolved against the meeting date, or null")
    evidence: str = Field(min_length=3, description="Short exact quote from the note")
    confidence: Confidence
    flags: List[Flag] = Field(default_factory=list)
    notes: str = Field(default="", description="Explains any judgment call")

    @field_validator("candidate_owner")
    @classmethod
    def owner_in_directory(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        team = (info.context or {}).get("team_names")
        if v is not None and team is not None and v not in team:
            raise ValueError(
                f"candidate_owner {v!r} is not a full name in team.csv. "
                "Use an exact name from the directory, or null with the ambiguous_owner flag."
            )
        return v

    @field_validator("evidence")
    @classmethod
    def evidence_is_quoted(cls, v: str, info: ValidationInfo) -> str:
        note = (info.context or {}).get("note_text")
        if note is not None and _squash(v) not in _squash(note):
            raise ValueError(
                f"evidence {v!r} does not appear verbatim in the note. Quote the note exactly."
            )
        return v

    @model_validator(mode="after")
    def flags_match_fields(self) -> "Item":
        if self.kind == "action":
            if self.candidate_owner is None and "ambiguous_owner" not in self.flags:
                raise ValueError(f"action {self.summary!r} has no owner but is missing the ambiguous_owner flag")
            if self.due is None and "no_date" not in self.flags:
                raise ValueError(f"action {self.summary!r} has no due date but is missing the no_date flag")
        return self


class HandoffPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_file: str
    meeting_date: date
    project: str = Field(description="Project name from projects.csv")
    attendees: List[str]
    items: List[Item]

    @field_validator("project")
    @classmethod
    def project_known(cls, v: str, info: ValidationInfo) -> str:
        projects = (info.context or {}).get("projects")
        if projects is not None and v not in projects:
            raise ValueError(f"project {v!r} is not in projects.csv ({sorted(projects)})")
        return v

    def assign_item_ids(self) -> "HandoffPacket":
        """Give every item a stable id like 2026-10-13#04 (meeting date + position in the packet)."""
        for n, item in enumerate(self.items, start=1):
            item.id = f"{self.meeting_date.isoformat()}#{n:02d}"
        return self


# ---------------------------------------------------------------------------
# Agent two's output: one planned change per action item (or group of items).
# ---------------------------------------------------------------------------

Op = Literal["create", "update", "no_change", "flag_only", "route_private"]
Priority = Literal["P1", "P2", "P3"]
Status = Literal["To do", "In progress", "Done"]


class Outbound(BaseModel):
    """A draft message to a client. Never sent for real: approved drafts go to outbox/sent/."""

    model_config = ConfigDict(extra="forbid")

    to: List[str] = Field(min_length=1, description="Client recipients, e.g. 'Hollis Greer (Cascadia)'")
    subject: str = Field(min_length=3)
    body: str = Field(min_length=10)


class PlannedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str = Field(default="", description="Set by the system. Leave empty.")
    source_item_ids: List[str] = Field(min_length=1, description="Handoff item ids this change comes from")
    op: Op
    matches_task: Optional[str] = Field(
        default=None, description="Existing task id (T-#### or NEW-#) for update/no_change; null for create"
    )
    title: Optional[str] = Field(default=None, max_length=79, description="Verb first, under 80 characters")
    project: Optional[str] = Field(default=None, description="Board to file on: a project from projects.csv")
    owner: Optional[str] = Field(default=None, description="Full name from team.csv")
    due: Optional[date] = None
    priority: Optional[Priority] = None
    status: Optional[Status] = None
    reason: str = Field(min_length=5, description="One sentence")
    needs_approval_from: List[str] = Field(default_factory=list)
    outbound: Optional[Outbound] = None

    @field_validator("owner")
    @classmethod
    def owner_in_directory(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        team = (info.context or {}).get("team_names")
        if v is not None and team is not None and v not in team:
            raise ValueError(f"owner {v!r} is not a full name in team.csv")
        return v

    @field_validator("needs_approval_from")
    @classmethod
    def approvers_in_directory(cls, v: List[str], info: ValidationInfo) -> List[str]:
        team = (info.context or {}).get("team_names")
        if team is not None:
            unknown = [name for name in v if name not in team]
            if unknown:
                raise ValueError(f"needs_approval_from has names not in team.csv: {unknown}")
        return v

    @field_validator("project")
    @classmethod
    def project_known(cls, v: Optional[str], info: ValidationInfo) -> Optional[str]:
        projects = (info.context or {}).get("projects")
        if v is not None and projects is not None and v not in projects:
            raise ValueError(f"project {v!r} is not in projects.csv ({sorted(projects)})")
        return v

    @model_validator(mode="after")
    def op_fields_consistent(self, info: ValidationInfo) -> "PlannedChange":
        board_ids = (info.context or {}).get("board_ids")
        if self.op == "create":
            if self.matches_task is not None:
                raise ValueError(f"create {self.title!r} must not set matches_task (use update instead)")
            if not self.title or not self.project:
                raise ValueError("create needs at least a title and a project")
        if self.op in ("update", "no_change"):
            if not self.matches_task:
                raise ValueError(f"{self.op} needs matches_task (the existing task id)")
            if board_ids is not None and self.matches_task not in board_ids:
                raise ValueError(f"matches_task {self.matches_task!r} is not on the board")
        return self


class FilerPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meeting_file: str
    changes: List[PlannedChange]

    @model_validator(mode="after")
    def every_action_covered(self, info: ValidationInfo) -> "FilerPlan":
        ctx = info.context or {}
        item_ids, action_ids = ctx.get("item_ids"), ctx.get("action_ids")
        used = {i for change in self.changes for i in change.source_item_ids}
        if item_ids is not None and used - set(item_ids):
            raise ValueError(f"source_item_ids not in this packet: {sorted(used - set(item_ids))}")
        if action_ids is not None and set(action_ids) - used:
            raise ValueError(f"action items with no planned change: {sorted(set(action_ids) - used)}")
        return self
