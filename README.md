# Halden & Moore: notes-to-board agents

A two-agent pipeline for the Tallwave case study. Agent one reads meeting notes and writes a
structured **handoff packet**. Agent two (not built yet) will file tasks on the board from those
packets only, never from the notes.

```
mock-data-pack/notes/*.md ──► agent one (src/notes_reader.py) ──► handoff/<note>.json ──► agent two (next step)
                                    │ fails validation twice
                                    └──► handoff/failed/<note>.raw.txt + .error.txt
```

## Layout

| Path | What it is |
|---|---|
| `src/schema.py` | Pydantic handoff packet: the contract between the two agents |
| `src/notes_reader.py` | Agent one: one note in, one validated packet out |
| `run_reader.py` | Runs agent one over all four notes in date order |
| `tests/expected_items.md` | Hand-written answer key for checking agent one |
| `handoff/` | Agent one's output (one JSON per note) |
| `mock-data-pack/` | The case-study data, unchanged |

## Run it

Requires Python 3.9+.

```bash
cd ~/Desktop/Tallwave_Agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then put your ANTHROPIC_API_KEY in .env
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
