# Demo: 5-minute run script

**Setup, before recording.** Open one terminal at full width:

```bash
cd ~/Desktop/Tallwave_Agent && source .venv/bin/activate && clear
```

Use fresh run names each rehearsal (`demo-dry`, `demo-live`, then `demo-dry2`, …). A run refuses to overwrite an existing folder.

**Timing.** Agent one takes about 25 s per note (about 100 s for all four). Agent two takes a similar time per packet. Steps marked **⏩ cut** are waits: trim them in the edit, or keep talking over them. Spoken time without the waits is about 4½ minutes.

---

### 1. Reset the board (0:00)

```bash
python scripts/reset_board.py
```
**Point at:** "13 tasks in board/board.csv".
**Say:** "This is Halden & Moore's task board as of October 29, straight from the data pack, and it's the only thing this pipeline can write to."

### 2. Agent one reads the notes (0:15) ⏩ cut

```bash
python run_reader.py
```
**Point at:** four `PATH: OK` lines and the flag counts (`injection=1`, `sensitive_hr=1`).
**Say:** "Agent one reads each meeting note and hands over a structured packet, one per meeting. It has no tools and can't touch the board."

### 3. Show a packet (0:40)

```bash
python scripts/show_packet.py meridian
```
**Point at, in order:**
1. `#08 staging`: `owner=None`, `ambiguous_owner`, because the note says "Jordan" and the firm has two.
2. `#06 Ravi call`: `due=2026-11-02`, because "before the 3rd" means done by the 2nd.
3. `#11`: the `injection` flag on the chat-log line that tried to give the AI orders.
4. Every `evidence:` line.

**Say:** "This packet is the handoff contract. Every item quotes the note word for word, and validation rejects any quote that isn't really in the note, so agent one can't invent work."

### 4. Show the guard (1:10)

```bash
python tests/test_guard.py 2>&1 | tail -4
```
**Point at:** the `GUARD: agent two tried to read the meeting notes` banner, then `PASS`.
**Say:** "Agent two must work from the packet, not the notes, and that's enforced in code: if anything in its process opens the notes folder, the run stops."

### 5. Dry run (1:30) ⏩ cut

```bash
python run_pipeline.py --dry-run --skip-reader --run-name demo-dry
```
**Point at:**
- One `CREATE` with `HOLD for Dana Whitfield, Lena Fischer`: the third revision round.
- One `POLICY [...]` line.
- One `SAM REVIEW possible duplicate of ...` line.
- The summary at the end.

**Say:** "Agent two plans every change, then plain code applies the firm's rules on top. Where they disagree the code wins and logs why, and anything client-facing or costly is held for a named person."

### 6. Real run, approved by hand, with one rejection (2:10) ⏩ cut until the first prompt

```bash
python run_pipeline.py --skip-reader --run-name demo-live
```
At each prompt, type `a`, **except** at the third revision round ("Email Hollis … third … revision round"): there, type `a` for **Dana Whitfield** and `r` for **Lena Fischer**.

**Point at, on that prompt:** the `Needs:` reasons (partner approval for a cost commitment), both evidence quotes ("we'll absorb that, no problem"), and the unsent draft to Hollis.
**Say, as you reject:** "Dana promised the client free work in the meeting, but the firm's rules say only the partner can approve that, and Lena says no."

### 7. Board diff (3:10)

The run prints the board at the end. Scroll to `=== Board after this run`.
**Point at:**
- `~ T-1051`: Eli (who left), then `[Needs owner]`, then Jordan Pike, across two meetings.
- `~ T-1042`: the SOW due date moved.
- The new `*` rows. Budget and margin tasks are on **Internal**, not the client-visible Cascadia board.
- There's no third-revision-round task.

**Say:** "Every row traces back to an item id and an approver in changes.log, and the rejected promise never reached the board the client can see."

### 8. Outbox and Sam's list (3:40)

```bash
ls outbox/*/*demo-live*
cat outbox/dana/demo-live__C*.md
cat outbox/sam-flags/demo-live__review-list.md
```
**Point at:**
- `sent/`: approved drafts only, no real email.
- `dana/`: "the commitment you made to the client wasn't approved by Lena Fischer … needs a follow-up".
- `lena-private/`: the HR item, on no board.
- `sam-flags/`: the injection, plus his review list with default due dates and the possible duplicate.

**Say:** "Nothing gets dropped silently: what was rejected, what's unclear and what's sensitive each go to a named person."

### 9. Failure path (4:10) ⏩ cut

```bash
python run_reader.py --break-twice meridian
```
**Point at:** `PATH: FAILED … run continues`, the other three `OK` lines, and `wrote handoff/failed/…raw.txt`.
**Say:** "When a step fails twice we keep the raw output, file nothing from that meeting, and carry on with the rest. Agent two has the same path (`--break-filer-twice`), and Sam gets a list of what wasn't filed."

### 10. Close: "confidently wrong" (4:35)

```bash
python scripts/show_packet.py runs/01-before-fix 10-13 --only redirects
python scripts/show_packet.py runs/04-reader-final 10-13 --only redirects
```
**Point at:**
- **Before:** `owner=Jordan Pike  due=2026-11-18`, from "maybe him if he has room".
- **After:** `owner=None`, `ambiguous_owner, tentative`, and the notes field explaining why.

**Say:** "This is the first run, confidently wrong: a 'maybe' became an owner and a made-up date. We keep that run as a test case, and the fix is in three layers: the prompt, a code check that the owner was actually named, and a human approving anything the client will see."

---

**After the demo:** run `python run_reader.py` once more to rebuild the Meridian packet that step 9 removed.
