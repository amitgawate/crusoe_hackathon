"""Run a mission from the terminal: python -m factcheck.cli [file] [--goal "..."]"""
import argparse
import asyncio
import json
import sys

from .config import Config
from .orchestrator import Mission

SAMPLE_TEXT = (
    "A day on Venus is longer than its year. Bats are blind and find their way only by echolocation. "
    "Napoleon was unusually short, at about 5 feet 2 inches. "
    "The Great Wall of China is visible from the Moon with the naked eye. "
    "Taking vitamin C every day prevents the common cold."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file", nargs="?", help="text file to fact-check (default: built-in sample)")
    ap.add_argument("--goal", default="Fact-check this text")
    args = ap.parse_args()
    text = open(args.file).read() if args.file else SAMPLE_TEXT
    cfg = Config()

    async def emit(ev):
        t = ev["type"]
        if t == "agent" and ev["status"] == "said":
            print(f"  [{ev['claim']}] {ev['agent']:>10}: {ev['text'][:140]}")
        elif t == "search":
            print(f"  [{ev['claim']}] {'search':>10}: {ev['query']!r} -> {ev['count']} new")
        elif t == "check":
            mark = "PASS" if ev["passed"] else "FAIL"
            print(f"  [{ev['claim'] or 'mission'}] {'check':>10}: {mark} {ev['name']} ({ev['detail'][:100]})")
        elif t in ("decision", "loop"):
            print(f"  [{ev['claim']}] {'supervisor':>10}: {ev.get('action', 'loop').upper()} {ev.get('reason', ev.get('text'))}")
        elif t == "claim_done":
            print(f"  [{ev['claim']}] {'CLOSED':>10}: {ev['status']} - {ev['reason']}")
        elif t in ("mission_start", "plan", "claims"):
            print(f"== {t}: {json.dumps({k: v for k, v in ev.items() if k not in ('type', 't')})[:300]}")

    summary = asyncio.run(Mission(cfg, emit).run(text, args.goal))
    print("\n== RESULT")
    for c in summary["claims"]:
        v = c["verdict"] or {}
        print(f"{c['id']} [{c['status']}] {str(v.get('verdict', '-')).upper():<12} {c['text']}")
    print(f"done={summary['done']} escalations={summary['escalations']} stats={summary['stats']}")
    sys.exit(0 if summary["done"] else 1)


if __name__ == "__main__":
    main()
