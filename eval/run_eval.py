"""Score a playbook on labeled claims: python -m eval.run_eval [--playbook playbooks/v1.json] [--split dev|test|all]"""
import argparse
import asyncio
import json
from pathlib import Path

from factcheck.config import Config
from factcheck.orchestrator import Mission, load_playbook

ROOT = Path(__file__).resolve().parent


def load_items(split: str) -> list[dict]:
    items = json.loads((ROOT / "claims.json").read_text())["items"]
    return [i for i in items if split == "all" or i["split"] == split]


def is_correct(label: str, verdict: str | None) -> bool:
    if label == "true":
        return verdict == "true"
    return verdict in ("false", "misleading")


async def evaluate(playbook: dict, split: str, cfg: Config | None = None) -> dict:
    items = load_items(split)
    summary = await Mission(cfg or Config(), playbook=playbook).run_claims([i["claim"] for i in items])
    rows = []
    for item, c in zip(items, summary["claims"]):
        verdict = (c["verdict"] or {}).get("verdict") if c["status"] != "unverifiable" else "unverifiable"
        rows.append({
            "claim": item["claim"], "label": item["label"], "verdict": verdict, "status": c["status"],
            "correct": is_correct(item["label"], verdict),
            "explanation": (c["verdict"] or {}).get("explanation", ""),
            "queries": c["queries"], "judge_rounds": c["judge_rounds"],
        })
    score = sum(r["correct"] for r in rows) / max(1, len(rows))
    return {"playbook": playbook.get("version"), "split": split, "score": round(score, 3),
            "n": len(rows), "rows": rows, "stats": summary["stats"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--playbook", default="playbooks/current.json")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    args = ap.parse_args()
    result = asyncio.run(evaluate(load_playbook(args.playbook), args.split))
    out = ROOT / "results"
    out.mkdir(exist_ok=True)
    (out / f"v{result['playbook']}-{args.split}.json").write_text(json.dumps(result, indent=2))
    for r in result["rows"]:
        print(f"{'OK ' if r['correct'] else 'BAD'} label={r['label']:<5} got={str(r['verdict']):<12} {r['claim']}")
    print(f"\nplaybook v{result['playbook']} on {args.split}: {result['score']:.0%} ({result['n']} claims)")


if __name__ == "__main__":
    main()
