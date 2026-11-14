"""Build runs/<run>/report.html from the files a run already saved. Makes no model calls.

    python scripts/report.py runs/08b-interactive-reject

Reads plan.json, changes.log, rejected.json, board.before.csv, the board after the run, and the
outbox notes for that run. The board after the run is runs/<run>/board.after.csv, which the
pipeline saves as a copy of board/board.csv. board/board.csv itself is used only if that copy is
missing, because a later run overwrites it.

Anything routed to outbox/lena-private/ is counted by file name only. Those files are never
opened, and changes routed there are left out of every table.
"""

import csv
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTBOX = ROOT / "outbox"
PROJECTS = ROOT / "mock-data-pack" / "projects.csv"
LIVE_BOARD = ROOT / "board" / "board.csv"
FIELDS = ["title", "project", "owner", "status", "due", "priority"]


def esc(value) -> str:
    return html.escape("" if value is None else str(value))


def read_csv(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        return {row["id"]: row for row in csv.DictReader(f)}


def read_json(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def read_log(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load(run_dir: Path) -> dict:
    run = run_dir.name
    plan = read_json(run_dir / "plan.json", [])
    # Changes routed privately never appear in the report. Only their count does.
    private_ids = {row["change_id"] for row in plan
                   if row.get("lena_private") or row["after_policy"]["op"] == "route_private"}
    plan = [row for row in plan if row["change_id"] not in private_ids]
    log = [e for e in read_log(run_dir / "changes.log") if e.get("change_id") not in private_ids]
    after_path = run_dir / "board.after.csv"
    return {
        "run": run,
        "plan": plan,
        "log": log,
        "rejected": {r["change_id"]: r for r in read_json(run_dir / "rejected.json", [])},
        "before": read_csv(run_dir / "board.before.csv"),
        "after": read_csv(after_path if after_path.exists() else LIVE_BOARD),
        "after_source": "board.after.csv" if after_path.exists() else "board/board.csv",
        "visibility": {r["project"]: r["board_visibility"] for r in csv.DictReader(open(PROJECTS, newline=""))},
        "failed": sorted(p.name.replace(".plan.error.txt", "") for p in (run_dir / "failed").glob("*.plan.error.txt")),
        "private_count": len(list((OUTBOX / "lena-private").glob(f"{run}__*.md"))),  # names only, never opened
        "dry_run": not (run_dir / "changes.log").exists(),
    }


# ---------- derived facts ----------

def real_ids(data: dict) -> dict:
    """NEW-# placeholder -> real task id, from the plan's target ids and the change log."""
    created = {e["change_id"]: e["task_id"] for e in data["log"] if e["op"] == "create"}
    return {row["target_id"]: created[row["change_id"]]
            for row in data["plan"]
            if (row["target_id"] or "").startswith("NEW-") and row["change_id"] in created}


def map_ids(text: str, mapping: dict) -> str:
    return re.sub(r"NEW-\d+", lambda m: mapping.get(m.group(0), m.group(0)), text)


def packets(data: dict) -> list:
    dates = sorted({sid.split("#")[0] for row in data["plan"] for sid in row["after_policy"]["source_item_ids"]
                    if "#" in sid})
    failed_dates = {name[:10] for name in data["failed"]}
    return [(d, d not in failed_dates) for d in dates] + [(n, False) for n in data["failed"] if n[:10] not in dates]


def decisions(row: dict, data: dict) -> list:
    """(approver, decision) in the order they were asked. The gate stops at the first rejection."""
    cid, approvers = row["change_id"], row["approvers"]
    if cid in data["rejected"]:
        rejecter = data["rejected"][cid]["rejected_by"]
        out, seen = [], False
        for person in approvers:
            if person == rejecter:
                out.append((person, "rejected"))
                seen = True
            else:
                out.append((person, "not asked" if seen else "approved"))
        return out
    approved = next((e.get("approved_by", []) for e in data["log"] if e.get("change_id") == cid), None)
    if approved is None:
        return [(p, "dry run" if data["dry_run"] else "no record") for p in approvers]
    return [(p, "approved" if p in approved else "no record") for p in approvers]


def follow_up(cid: str, run: str):
    for folder in sorted(OUTBOX.iterdir()):
        if folder.name in ("lena-private", "sam-flags", "sent") or not folder.is_dir():
            continue
        note = folder / f"{run}__{cid}.md"
        if note.exists():
            return note
    return None


def badge(project: str, data: dict) -> str:
    vis = data["visibility"].get(project or "", "")
    if vis == "client-visible":
        return f'<span class="badge client">{esc(project)} · client-visible</span>'
    return f'<span class="badge internal">{esc(project or "no board")} · internal</span>'


def flag_kind(text: str) -> str:
    low = text.lower()
    if "instruct" in low or "injection" in low:
        return "Injection"
    if "failed to plan" in low:
        return "Unfiled packet"
    if "date" in low:
        return "Re-date"
    return "Flag"


def review_kind(note: str) -> str:
    if note.startswith("possible duplicate"):
        return "Possible duplicate"
    if note.startswith("default due date"):
        return "Default due date"
    return "Review"


# ---------- sections ----------

def header(data: dict) -> str:
    items = []
    for name, ok in packets(data):
        cls, label = ("ok", "filed") if ok else ("bad", "failed")
        items.append(f'<li class="{cls}"><span class="dot"></span>{esc(name)} <em>{label}</em></li>')
    filed = sum(1 for _, ok in packets(data) if ok)
    failed = sum(1 for _, ok in packets(data) if not ok)
    mode = "Dry run, nothing written" if data["dry_run"] else "Live run"
    return f"""
<header>
  <p class="eyebrow">Halden &amp; Moore · board filing run</p>
  <h1>{esc(data["run"])}</h1>
  <p class="sub">{mode} · {filed} packet{"s" if filed != 1 else ""} filed · {failed} failed</p>
  <ul class="packets">{"".join(items)}</ul>
</header>"""


def tiles(data: dict) -> str:
    created = sum(1 for e in data["log"] if e["op"] == "create")
    updated = sum(1 for e in data["log"] if e["op"] == "update")
    held = [r for r in data["plan"] if r["approvers"]]
    approved = sum(1 for r in held if all(d == "approved" for _, d in decisions(r, data)))
    rejected = len(data["rejected"])
    flags = sum(1 for r in data["plan"] if r.get("sam_flag"))
    review = sum(len(r["sam_review"]) for r in data["plan"] if r["change_id"] not in data["rejected"])
    cells = [
        ("Created", created, "new tasks"),
        ("Updated", updated, "task updates"),
        ("Approved", f"{approved}<small> of {len(held)}</small>", "held changes"),
        ("Rejected", rejected, "not written, not sent"),
        ("Flags for Sam", flags, f"+{review} on his review list"),
    ]
    return '<section class="tiles">' + "".join(
        f'<div class="tile{" warn" if label == "Rejected" and value else ""}"><p class="label">{label}</p>'
        f'<p class="value">{value}</p><p class="note">{note}</p></div>'
        for label, value, note in cells) + "</section>"


def gate(data: dict) -> str:
    mapping = real_ids(data)
    rows = []
    for row in data["plan"]:
        if not row["approvers"]:
            continue
        c = row["after_policy"]
        cid = row["change_id"]
        target = mapping.get(row["target_id"], row["target_id"]) or ""
        if target.startswith("NEW-"):
            target = ""  # never created
        title = c["title"] or data["after"].get(target, {}).get("title") or data["before"].get(target, {}).get("title", "")
        people = "".join(f'<li><span class="pill {d.replace(" ", "-")}">{esc(d)}</span> {esc(p)}</li>'
                         for p, d in decisions(row, data))
        ds = [d for _, d in decisions(row, data)]
        if "rejected" in ds:
            result = '<span class="pill rejected">rejected</span>'
        elif all(d == "approved" for d in ds):
            result = '<span class="pill approved">written</span>'
        else:
            result = f'<span class="pill not-asked">{esc(ds[0])}</span>'
        fu = ""
        if cid in data["rejected"]:
            note = follow_up(cid, data["run"])
            if note:
                lead = note.read_text().splitlines()[0].lstrip("# ").replace("For ", "")
                fu = f'Note to {esc(lead)}<br><code>{esc(note.relative_to(ROOT))}</code>'
            else:
                fu = '<span class="muted">none needed: the client hadn\'t heard it</span>'
        reasons = "".join(f"<li>{esc(r.split(': ', 1)[-1])}</li>" for r in row["approval_reasons"])
        rows.append(f"""<tr{' class="row-rejected"' if cid in data["rejected"] else ""}>
  <td><code>{esc(cid)}</code> <span class="op">{esc(c["op"])}{" " + esc(target) if target else ""}</span>
      <div class="title">{esc(title)}</div><ul class="reasons">{reasons}</ul></td>
  <td><ul class="people">{people}</ul></td>
  <td>{result}</td>
  <td>{fu or '<span class="muted">—</span>'}</td>
</tr>""")
    if not rows:
        return '<section><h2>Approval gate</h2><p class="muted">Nothing was held for approval.</p></section>'
    return f"""<section><h2>Approval gate</h2>
<p class="lede">Every change held for a named person, with each approver's answer. The gate stops at the first rejection.</p>
<div class="scroll"><table>
<thead><tr><th>Change</th><th>Approvers</th><th>Result</th><th>Follow-up if rejected</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></section>"""


def board_changes(data: dict) -> str:
    rows = []
    state = {tid: dict(row) for tid, row in data["before"].items()}  # the board as each write found it
    for e in data["log"]:
        tid = e["task_id"]
        if e["op"] == "create":
            t = e["task"]
            state[tid] = dict(t)
            rows.append(f"""<tr><td><code>{esc(tid)}</code><div class="op new">new</div></td>
  <td>{esc(t["title"])}</td><td>{esc(t["owner"])}</td><td>{esc(t["due"])}</td>
  <td>{badge(t["project"], data)}</td><td><code>{esc(e["change_id"])}</code></td></tr>""")
            continue
        before, after = e.get("before", {}), e.get("after", {})
        current = state.setdefault(tid, {})

        def cell(field):
            if field in after and before.get(field) != after.get(field):
                return (f'<span class="was">{esc(before.get(field) or "—")}</span>'
                        f'<span class="arrow">→</span><span class="now">{esc(after[field])}</span>')
            return esc(after.get(field, before.get(field, current.get(field, ""))))

        project = after.get("project") or before.get("project") or current.get("project", "")
        extra = [f for f in ("status", "priority", "project") if f in after and before.get(f) != after.get(f)]
        extras = "".join(f'<div class="minor">{f}: {cell(f)}</div>' for f in extra)
        rows.append(f"""<tr><td><code>{esc(tid)}</code><div class="op upd">updated</div></td>
  <td>{cell("title")}{extras}</td><td>{cell("owner")}</td><td>{cell("due")}</td>
  <td>{badge(project, data)}</td><td><code>{esc(e["change_id"])}</code></td></tr>""")
        current.update(after)
    if not rows:
        body = "Dry run: nothing was written." if data["dry_run"] else "No board changes."
        return f'<section><h2>Board changes</h2><p class="muted">{body}</p></section>'
    return f"""<section><h2>Board changes</h2>
<p class="lede">Every write in <code>changes.log</code>, in order. Updates show before → after. Final board: <code>{esc(data["after_source"])}</code>.</p>
<div class="scroll"><table>
<thead><tr><th>Task</th><th>Title</th><th>Owner</th><th>Due</th><th>Board</th><th>Change</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></section>"""


def needs_person(data: dict) -> str:
    mapping = real_ids(data)
    groups = {"Injection": [], "Unfiled packet": [], "Re-date": [], "Flag": [],
              "Possible duplicate": [], "Default due date": [], "Review": []}
    for row in data["plan"]:
        cid = row["change_id"]
        sources = ", ".join(row["after_policy"]["source_item_ids"])
        if row.get("sam_flag"):
            groups[flag_kind(row["sam_flag"])].append(
                f'<li><code>{esc(cid)}</code> {esc(row["sam_flag"])} <span class="muted">({esc(sources)})</span></li>')
        target = mapping.get(row["target_id"], row["target_id"]) or ""
        if cid in data["rejected"]:
            continue  # rejected: no task was written, so there is nothing to review
        for note in row["sam_review"]:
            note = map_ids(note, mapping)
            groups[review_kind(note)].append(
                f'<li><code>{esc(cid)}</code>{" <code>" + esc(target) + "</code>" if target else ""} {esc(note)}</li>')
    blocks = "".join(
        f'<div class="group"><h3>{esc(kind)} <span class="count">{len(items)}</span></h3><ul>{"".join(items)}</ul></div>'
        for kind, items in groups.items() if items)
    private = data["private_count"]
    blocks += (f'<div class="group private"><h3>Routed privately to Lena Fischer <span class="count">{private}</span></h3>'
               f'<p class="muted">Sensitive items are counted here and never shown. They are not on any board.</p></div>')
    return f"""<section><h2>Needs a person</h2>
<p class="lede">All of these go to Sam Okafor unless marked otherwise. Nothing in this list was acted on by the agents.</p>
{blocks}</section>"""


CSS = """
:root{--bg:#f6f5f1;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a64;--line:#e3e1da;--accent:#2d5b8a;
--ok:#2f7d4f;--ok-bg:#e6f2ea;--bad:#b23a32;--bad-bg:#f8e7e5;--warn-bg:#fbf0dc;--warn:#8a5a12;
--client:#7a3e9d;--client-bg:#f1e8f6;--int:#3d5f6e;--int-bg:#e5eef1;--code:#efede7}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;--bg:#151514;--panel:#1e1e1c;
--ink:#ecebe6;--muted:#9c9a92;--line:#33322f;--accent:#8fb6de;--ok:#7fcf9c;--ok-bg:#1f3428;--bad:#f0948c;
--bad-bg:#3a2220;--warn-bg:#3a2f1a;--warn:#e6c27a;--client:#d3a6ec;--client-bg:#33253c;--int:#9cc3d3;--int-bg:#1f2d33;--code:#2a2a27}}
:root[data-theme="dark"]{color-scheme:dark;--bg:#151514;--panel:#1e1e1c;--ink:#ecebe6;--muted:#9c9a92;--line:#33322f;
--accent:#8fb6de;--ok:#7fcf9c;--ok-bg:#1f3428;--bad:#f0948c;--bad-bg:#3a2220;--warn-bg:#3a2f1a;--warn:#e6c27a;
--client:#d3a6ec;--client-bg:#33253c;--int:#9cc3d3;--int-bg:#1f2d33;--code:#2a2a27}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
padding:40px 20px 80px}
main{max-width:1080px;margin:0 auto}
header{margin-bottom:28px}
.eyebrow{margin:0;color:var(--muted);font-size:13px;letter-spacing:.04em;text-transform:uppercase}
h1{margin:4px 0 2px;font-size:32px;letter-spacing:-.01em}
.sub{margin:0 0 14px;color:var(--muted)}
.packets{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:8px}
.packets li{background:var(--panel);border:1px solid var(--line);border-radius:999px;padding:4px 12px 4px 10px;font-size:13px}
.packets em{font-style:normal;color:var(--muted)}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--ok)}
.packets .bad .dot{background:var(--bad)} .packets .bad em{color:var(--bad)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:36px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile .label{margin:0;font-size:13px;color:var(--muted)}
.tile .value{margin:2px 0;font-size:34px;font-weight:650;font-variant-numeric:tabular-nums;line-height:1.15}
.tile .value small{font-size:16px;font-weight:500;color:var(--muted)}
.tile .note{margin:0;font-size:12px;color:var(--muted)}
.tile.warn{border-color:var(--bad);background:var(--bad-bg)}
section{margin-bottom:40px}
h2{font-size:20px;margin:0 0 4px}
.lede{margin:0 0 14px;color:var(--muted);font-size:14px}
.scroll{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:12px}
table{border-collapse:collapse;width:100%;min-width:760px;font-size:14px}
th{text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;
padding:10px 14px;border-bottom:1px solid var(--line)}
td{padding:12px 14px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:0}
tr.row-rejected td{background:var(--bad-bg)}
code{font:12.5px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);padding:1px 5px;border-radius:4px}
.title{margin-top:4px;font-weight:550}
.op{font-size:12px;color:var(--muted);text-transform:uppercase;letter-spacing:.03em}
.op.new{color:var(--ok)} .op.upd{color:var(--accent)}
.reasons{margin:4px 0 0;padding-left:18px;color:var(--muted);font-size:13px}
.people{list-style:none;margin:0;padding:0}
.people li{margin-bottom:4px;white-space:nowrap}
.pill{display:inline-block;font-size:12px;font-weight:600;padding:1px 8px;border-radius:999px;min-width:66px;text-align:center}
.pill.approved{background:var(--ok-bg);color:var(--ok)}
.pill.rejected{background:var(--bad);color:var(--panel)}
.pill.not-asked,.pill.dry-run,.pill.no-record{background:var(--code);color:var(--muted)}
.badge{display:inline-block;font-size:12px;padding:2px 8px;border-radius:6px;white-space:nowrap}
.badge.client{background:var(--client-bg);color:var(--client)}
.badge.internal{background:var(--int-bg);color:var(--int)}
.was{color:var(--muted);text-decoration:line-through}
.arrow{margin:0 6px;color:var(--muted)}
.now{font-weight:600}
.minor{font-size:13px;margin-top:4px;color:var(--muted)}
.muted{color:var(--muted)}
.group{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:12px}
.group h3{margin:0 0 6px;font-size:15px}
.group ul{margin:0;padding-left:18px}
.group li{margin-bottom:6px}
.count{display:inline-block;font-size:12px;background:var(--warn-bg);color:var(--warn);border-radius:999px;padding:0 8px;margin-left:4px}
.group.private{border-style:dashed}
.group.private p{margin:0}
footer{color:var(--muted);font-size:12px}
@media (max-width:520px){body{padding:24px 16px 60px}h1{font-size:26px}.tile .value{font-size:28px}}
"""


def build(run_dir: Path) -> Path:
    data = load(run_dir)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Run report · {esc(data["run"])}</title><style>{CSS}</style></head>
<body><main>
{header(data)}
{tiles(data)}
{gate(data)}
{board_changes(data)}
{needs_person(data)}
<footer>Built by scripts/report.py from runs/{esc(data["run"])}/ and outbox/*/{esc(data["run"])}__*. No model calls.</footer>
</main></body></html>
"""
    out = run_dir / "report.html"
    out.write_text(page)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python scripts/report.py runs/<run>")
    run_dir = (ROOT / sys.argv[1]).resolve()
    if not (run_dir / "plan.json").exists():
        sys.exit(f"no plan.json in {run_dir}")
    print(f"wrote {build(run_dir).relative_to(ROOT)}")
