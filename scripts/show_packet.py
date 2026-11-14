"""Print a handoff packet readably.

    python scripts/show_packet.py meridian                     packet in handoff/ matching "meridian"
    python scripts/show_packet.py runs/01-before-fix 10-13 --only redirects
"""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

parser = argparse.ArgumentParser()
parser.add_argument("where", help="a folder of packets, or a note name to find in handoff/")
parser.add_argument("match", nargs="?", default="", help="part of the packet file name")
parser.add_argument("--only", default="", help="only items whose summary contains this word")
args = parser.parse_args()

folder = ROOT / args.where if (ROOT / args.where).is_dir() else ROOT / "handoff"
match = args.match if folder != ROOT / "handoff" or args.match else args.where
path = next(p for p in sorted(folder.glob("*.json")) if match in p.name)
packet = json.loads(path.read_text())
print(f"{path.relative_to(ROOT)}   project={packet['project']}   date={packet['meeting_date']}\n")
for item in packet["items"]:
    if args.only.lower() not in item["summary"].lower():
        continue
    print(f"{item.get('id') or '-':<14} {item['kind']:<8} {item['summary']}")
    print(f"{'':<14} owner={item['candidate_owner']}  due={item['due']}  confidence={item['confidence']}  "
          f"flags={','.join(item['flags']) or '-'}")
    print(f"{'':<14} evidence: \"{item['evidence']}\"")
    if args.only:
        print(f"{'':<14} notes: {item['notes']}")
    print()
