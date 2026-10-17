"""Version N+1: the team reviews its own mistakes, rewrites its prompts, and keeps the change only if it scores better.

python -m eval.improve [--rounds 1]

Each round: score the current playbook on dev -> an improver agent reads the failures and rewrites prompts ->
score the candidate on dev -> keep it only if dev improves. The held-out test split is scored before and after,
so the gain is measured on claims the improver never saw.
"""
import argparse
import asyncio
import json
import time
from pathlib import Path

from factcheck.config import Config
from factcheck.llm import LLM
from factcheck.orchestrator import load_playbook
from .run_eval import evaluate

PLAYBOOKS = Path("playbooks")
EDITABLE = ["query_writer", "advocate", "skeptic", "judge", "verifier"]

IMPROVER = (
    "You improve a fact-checking agent team by rewriting its prompts. You get the current prompts and the claims it "
    "got wrong on a dev set (with the right label, its verdict, its explanation and its search queries). Diagnose the "
    "general failure pattern, then rewrite only the prompts that caused it. Fix the pattern, never the specific "
    "claims: do not mention any claim, fact or answer from the failures. Each prompt must still demand the same JSON "
    "reply format it demands now. Reply with JSON only:\n"
    "{\"diagnosis\": str, \"changes\": {\"<role>\": \"<full new prompt>\"}}  (roles: " + ", ".join(EDITABLE) + ")"
)


def next_version() -> int:
    nums = [int(p.stem[1:]) for p in PLAYBOOKS.glob("v*.json") if p.stem[1:].isdigit()]
    return max(nums, default=0) + 1


async def improve_once(cfg: Config) -> dict:
    current = load_playbook(str(PLAYBOOKS / "current.json"))
    print(f"Scoring v{current['version']} on dev and test...")
    base_dev, base_test = await evaluate(current, "dev", cfg), await evaluate(current, "test", cfg)
    print(f"  v{current['version']}: dev {base_dev['score']:.0%}, test {base_test['score']:.0%}")
    failures = [r for r in base_dev["rows"] if not r["correct"]]
    if not failures:
        return {"kept": False, "reason": "No dev failures to learn from.", "base_test": base_test["score"]}

    llm = LLM(cfg)
    prompts = json.dumps({k: current["prompts"][k] for k in EDITABLE}, indent=2)
    fails = json.dumps([{k: r[k] for k in ("claim", "label", "verdict", "explanation", "queries")} for r in failures],
                       indent=2)
    out = await llm.json("improver", IMPROVER, f"Current prompts:\n{prompts}\n\nFailures:\n{fails}", strong=True)
    changes = {k: v for k, v in (out.get("changes") or {}).items() if k in EDITABLE and isinstance(v, str) and v.strip()}
    print(f"Improver diagnosis: {out.get('diagnosis', '')}\n  changed: {list(changes) or 'nothing'}")
    if not changes:
        return {"kept": False, "reason": "Improver proposed no changes.", "base_test": base_test["score"]}

    v = next_version()
    candidate = {"version": v, "parent": current["version"], "notes": out.get("diagnosis", ""),
                 "prompts": {**current["prompts"], **changes}}
    cand_dev = await evaluate(candidate, "dev", cfg)
    print(f"  candidate v{v}: dev {cand_dev['score']:.0%}")
    record = {"t": time.time(), "parent": current["version"], "candidate": v, "diagnosis": candidate["notes"],
              "changed": list(changes), "dev_before": base_dev["score"], "dev_after": cand_dev["score"],
              "test_before": base_test["score"]}
    (PLAYBOOKS / f"v{v}.json").write_text(json.dumps(candidate, indent=2))
    if cand_dev["score"] > base_dev["score"]:
        cand_test = await evaluate(candidate, "test", cfg)
        (PLAYBOOKS / "current.json").write_text(json.dumps({"use": f"v{v}.json"}))
        record.update(kept=True, test_after=cand_test["score"])
        print(f"  KEPT v{v}. Held-out test: {base_test['score']:.0%} -> {cand_test['score']:.0%}")
    else:
        record.update(kept=False)
        print(f"  Discarded v{v}: dev did not improve.")
    with open(PLAYBOOKS / "history.jsonl", "a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=1)
    args = ap.parse_args()
    cfg = Config()
    for i in range(args.rounds):
        print(f"\n=== Round {i + 1} ===")
        asyncio.run(improve_once(cfg))


if __name__ == "__main__":
    main()
