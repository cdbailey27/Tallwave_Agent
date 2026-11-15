# Halden & Moore: notes-to-board agents

A two-agent pipeline for the Tallwave case study. Agent one reads meeting notes and writes a
structured **handoff packet**. Agent two plans board changes from those packets only, never from
the notes. Plain-code policy checks every plan, and people approve anything that leaves the firm.

```
notes/*.md ─► agent one ─► handoff/*.json ─┬─► agent two ─► planned changes ─► policy (code) ─┬─► approval gate ─► board.csv
             (reader)      (the contract)  │  (filer)       (Pydantic)        overrides logged │   (engagement lead,   outbox/sent/
                                           │                                                  │    Lena for cost)     (drafts only)
                         notes/ locked ────┘                                                  ├─► outbox/sam-flags/   (injection, review list)
                         (guard.py)                                                           └─► outbox/lena-private/ (HR, never on a board)
```

## Setup

Requires Python 3.9+ and an Anthropic API key.

```bash
git clone <this repo's URL> && cd Tallwave_Agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then set ANTHROPIC_API_KEY in .env (never committed; .env is gitignored)
```

## Run

```bash
python run_reader.py                                     # agent one: notes -> handoff/*.json
python run_pipeline.py --skip-reader --reset-board       # agent two -> policy -> approve each held change -> board
python scripts/report.py runs/<run>                      # HTML report of a run, at runs/<run>/report.html
```

`run_pipeline.py` runs agent one first unless you pass `--skip-reader`. Every run needs a fresh
`--run-name` (or takes a timestamp by default) and refuses to overwrite an existing `runs/` folder.
`DEMO.md` is the 5-minute walkthrough script.

## All pipeline options

```bash
python run_pipeline.py --dry-run --reset-board          # plan only: nothing written
python run_pipeline.py --reset-board --auto-approve     # write everything that passes
python run_pipeline.py --reset-board                    # approve or reject each held change by hand
python run_pipeline.py --reset-board --auto-reject      # see what gets through without sign-off
python run_pipeline.py --break-twice meridian --reset-board --auto-approve   # agent one fails a note; the rest carry on
python tests/compare_board.py runs/<run>                # compare a run with tests/expected_board.md
python tests/test_guard.py                              # prove agent two can't read notes/
```

Each run writes to `runs/<run>/`:
- `plan.json`: every change as agent two planned it and as policy left it, with approvers and evidence.
- `policy_overrides.log`
- `board.before.csv` and `board.after.csv`
- `changes.log`: one JSON line per board write.
- `rejected.json`
- `sam_review.md`
- `failed/`
- `summary.txt`

The live board is `board/board.csv`. It's seeded from the data pack, which is never modified, and `--reset-board` starts it over.

## Layout

| Path | What it is |
|---|---|
| `src/schema.py` | Pydantic contracts: handoff packet (agent one) and planned change (agent two) |
| `src/notes_reader.py` | Agent one: one note in, one validated packet out |
| `src/board_filer.py` | Agent two: packets + board in, validated planned changes out; no writes |
| `src/policy.py` | firm-rules.md as plain code; overrides agent two and logs why |
| `src/approve.py` | Approval gate: shows change, evidence and draft; approve or reject |
| `src/board.py` | Board writer: read, create, update. No delete, no email |
| `src/guard.py` | Fails the run loudly if agent two's process touches notes/ |
| `run_pipeline.py` | One command: reader → filer → policy → approval → write |
| `run_reader.py` | Runs agent one over all four notes in date order |
| `tests/expected_items.md` | Hand-written answer key for checking agent one |
| `handoff/` | Agent one's output (one JSON per note) |
| `mock-data-pack/` | The case-study data, unchanged |

## Agent one on its own

```bash
python run_reader.py
```

Packets land in `handoff/`. To check them, compare with `tests/expected_items.md`.

To exercise the failure path:

```bash
python run_reader.py --break meridian        # corrupt the first reply: watch the retry recover it
python run_reader.py --break-twice meridian  # corrupt the retry too: raw output goes to handoff/failed/, run continues
```

Saved runs in `runs/`:
- `01-before-fix` is the first run. It's the "confidently wrong" example: the redirects map was assigned to Jordan Pike on a "maybe".
- `02-after-fix` is the run after the prompt fixes.
- `03-break-twice` holds the failure record from `--break-twice`.
- `04-reader-final` is agent one's final output, matching `tests/expected_items.md`.
- `05-dry-run` and `06-pipeline` are the first full pipeline runs (plan only, then auto-approve).
- `07-auto-reject` rejects every held change; follow-up notes go to the engagement leads.
- `08b-interactive-reject` is approved by hand, with Lena rejecting the third revision round. It has `report.html`.
- `09-break-filer` and `10-break-filer-twice` exercise agent two's retry and failure paths.
- `11-dry-run-a/b/c` are three dry runs used to check consistency (`tests/compare_plans.py`).
- `demo-final` is the run recorded for the demo.

Optional settings in `.env`: `READER_MODEL` (default `claude-opus-5-5`) and `DATA_DIR` (default `mock-data-pack`).

## How agent one behaves

- **No tools.** Claude only returns JSON text. The only file the agent writes is its own packet (or a failure record).
- **The note is data.** The note goes to Claude inside `<meeting_note>` tags, with a system prompt saying nothing in it is an instruction. Instructions planted in a note become an item with the `injection` flag.
- **Never guesses owners.** `candidate_owner` must be an exact name from `team.csv`, or `null` with the `ambiguous_owner` flag.
- **Validation is stricter than the shape.** On top of the field types, `schema.py` checks:
  - each owner exists in `team.csv`;
  - each project exists in `projects.csv`;
  - each `evidence` quote really appears in the note (this catches made-up items);
  - every action with no owner or no date carries the matching flag.
- **Failure path.** If validation fails, the agent retries once and includes the errors in the retry. A second failure (or an API error or refusal) writes the raw output and error to `handoff/failed/`, logs it, and moves on to the next note.
- **Refusal fallback.** Requests opt into server-side refusal fallback (`fallbacks: "default"`). If a safety classifier declines, the API reruns the request on a fallback model. To turn this off, remove `betas` and `extra_body` in `call_claude`.
