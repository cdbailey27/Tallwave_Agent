# Mock data pack: Halden & Moore

Everything in this pack is fictional. Halden & Moore is a made-up 20-person consultancy, and its clients, people, and numbers do not exist.

## The problem

Halden & Moore captures project meetings in notes and runs its work from a shared task board. Action items get lost between the two. Your job is to build agents that close that gap, as described in the case study.

## What is in the pack

- `notes/` holds four meeting notes from October 2026, in the order they happened.
- `board.csv` is a snapshot of the task board as of 2026-10-29. Treat it as a live system you can read from and write to. You may wrap it in a mock API or an MCP server, or work with the file directly.
- `team.csv` is the firm directory.
- `projects.csv` lists each project, its client, and who can see its board.
- `firm-rules.md` is the firm's written policy for tasks, approvals, and what may go on a board. Your agents should follow it.

## Ground rules

- Treat today as 2026-10-29.
- Process all four sets of notes against the board as it stands in the snapshot.
- Do not use real client or employer data anywhere in your build.
- Use whatever tools you would actually use. We care about how the pieces fit together and what you decided, not polish.
