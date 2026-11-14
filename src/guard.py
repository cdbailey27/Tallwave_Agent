"""Hard guard: agent two must never read the meeting notes.

Agent two works only from the handoff packets. This guard enforces that in code, not just in
the prompt. Inside `notes_forbidden(...)`, any attempt by this process to open a file under
notes/, or to list that folder, raises NotesAccessError and prints a loud message.

It uses a Python audit hook (PEP 578), which sees every file open: builtins.open, io.open,
pathlib, os.open, os.listdir and os.scandir. Audit hooks can't be removed once installed, so the
hook stays installed and only checks while the guard is switched on.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional


class NotesAccessError(BaseException):
    """A BaseException on purpose, like KeyboardInterrupt: no `except Exception` (agent two's own
    retry handler included) and no library that tolerates PermissionError (pathlib.glob) can
    swallow it. A notes read always stops the run."""


_forbidden_dir: Optional[str] = None
_installed = False

_WATCHED_EVENTS = {"open", "os.listdir", "os.scandir"}


def _hook(event: str, args: tuple) -> None:
    if _forbidden_dir is None or event not in _WATCHED_EVENTS:
        return
    target = args[0] if args else None
    if target is None or isinstance(target, int):  # scandir() of cwd, or an already-open fd
        return
    try:
        path = os.path.abspath(os.fsdecode(target))  # string-only; no file system calls inside the hook
    except (TypeError, ValueError):
        return
    if path == _forbidden_dir or path.startswith(_forbidden_dir + os.sep):
        message = (
            "\n" + "!" * 72 + "\n"
            f"GUARD: agent two tried to read the meeting notes ({event}: {path}).\n"
            "Agent two may only use handoff packets, the board, team, projects and firm rules.\n"
            + "!" * 72
        )
        print(message, file=sys.stderr)
        raise NotesAccessError(message)


@contextmanager
def notes_forbidden(notes_dir: Path) -> Iterator[None]:
    """Block every read of notes_dir for the duration of the with-block."""
    global _forbidden_dir, _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True
    _forbidden_dir = os.path.abspath(notes_dir)
    try:
        yield
    finally:
        _forbidden_dir = None
