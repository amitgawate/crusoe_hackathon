"""Small FEVER judge-only experiment. Gold evidence is fixed; retrieval is not evaluated."""
import asyncio
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from factcheck.config import Config
from factcheck.llm import LLM
from factcheck.orchestrator import load_playbook

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "fever_sample.json"
LABELS = {"SUPPORTS": "true", "REFUTES": "false", "NOT ENOUGH INFO": "unverifiable"}
STAGES = ["baseline_dev", "improve", "candidate_dev", "baseline_test", "candidate_test"]


def select_sample(rows, per_label=5):
    """Freeze balanced splits without repeated claims or shared evidence pages."""
    selected, used_ids, used_pages = [], set(), set()
    ordered = sorted(rows, key=lambda r: hashlib.sha256(str(r["id"]).encode()).hexdigest())
    for split in ("dev", "test"):
        for label in LABELS:
            count = 0
            for row in ordered:
                pages = {e[0] for e in row["evidence"]}
                if (row["label"] != label or row["original_id"] in used_ids
                        or not pages or pages & used_pages):
                    continue
                selected.append({**row, "split": split})
                used_ids.add(row["original_id"])
                used_pages.update(pages)
                count += 1
                if count == per_label:
                    break
            if count != per_label:
                raise ValueError("Not enough independent claims for balanced splits")
    return selected


def judge_input(row):
    """Only the claim and evidence reach the judge, never benchmark annotations."""
    evidence = "\n".join(f"[{i + 1}] {e[0]} (wikipedia.org)\n{e[2]}"
                         for i, e in enumerate(row["evidence"]))
    return f"Claim: {row['claim']}\n\nEvidence:\n{evidence}"


def score_rows(rows, total):
    correct = sum(not r.get("error") and r.get("verdict") == LABELS[r["label"]] for r in rows)
    return {"correct": correct, "completed": len(rows), "total": total,
            "accuracy": round(100 * correct / len(rows), 1) if rows else None,
            "errors": sum(bool(r.get("error")) for r in rows)}


class Experiment:
    """One local experiment at a time, with snapshots that survive browser refresh."""
    def __init__(self, dataset=DATASET, output=ROOT / "results" / "fever"):
        self.dataset = Path(dataset)
        self.output = Path(output)
        self.task = None
        self.state = {"status": "idle", "stage": None, "results": {}, "events": []}
        latest = self.output / "latest.json"
        if latest.exists():
            self.state = json.loads(latest.read_text())
            if self.state["status"] == "running":
                self.state.update(status="interrupted", message="Server restarted; start a new experiment.")

    def snapshot(self):
        data = json.loads(self.dataset.read_text())
        return {**self.state, "dataset": data["metadata"], "items": data["items"], "stages": STAGES}

    def save(self):
        self.output.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(self.state, indent=2)
        for path in (self.output / "latest.json", self.output / f"{self.state['id']}.json"):
            temp = path.with_suffix(".tmp")
            temp.write_text(raw)
            temp.replace(path)

    def event(self, message):
        self.state["events"].append({"time": time.time(), "message": message})
        self.save()

    def start(self, cfg=None):
        if self.task and not self.task.done():
            raise ValueError("An experiment is already running")
        cfg = cfg or Config(mock=os.environ.get("FC_MOCK") == "1")
        if cfg.mock:
            raise ValueError("Live API credentials are required; mock results are not benchmark evidence")
        if not cfg.crusoe_api_key:
            raise ValueError("Live Crusoe API credentials are required")
        data = json.loads(self.dataset.read_text())
        playbook = load_playbook(cfg.playbook)
        self.state = {"id": uuid.uuid4().hex[:12], "status": "running", "stage": "baseline_dev",
                      "started": time.time(), "ended": None, "results": {}, "events": [],
                      "model": cfg.model_strong, "baseline_version": playbook["version"],
                      "baseline_prompt": playbook["prompts"]["judge"], "candidate_prompt": None,
                      "diagnosis": None, "calls": 0, "tokens": 0,
                      "dataset_sha256": hashlib.sha256(self.dataset.read_bytes()).hexdigest()}
        self.event("Experiment started. Fixed gold evidence; live judge; no web search.")
        self.task = asyncio.create_task(self.run(cfg, data["items"], playbook))

    async def stop(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            if self.state["status"] == "running":
                self.state.update(status="stopped", ended=time.time())
                self.event("Stopped before evaluation began.")

    async def run(self, cfg, items, playbook):
        llm = LLM(cfg)
        gate = asyncio.Semaphore(cfg.max_concurrency)

        async def evaluate(prompt, split, key):
            selected = [r for r in items if r["split"] == split]
            rows = []
            self.state["stage"] = key
            self.state["results"][key] = {"rows": rows, **score_rows(rows, len(selected))}
            self.event(f"Scoring {key.replace('_', ' ')}: {len(selected)} claims.")

            async def one(row):
                async with gate:
                    result = {"id": row["id"], "claim": row["claim"], "label": row["label"]}
                    try:
                        async with asyncio.timeout(100):
                            prediction = await llm.json("judge", prompt, judge_input(row), strong=True)
                        verdict = str(prediction.get("verdict", "")).lower().strip()
                        if verdict not in ("true", "false", "misleading", "unverifiable"):
                            raise ValueError("Invalid verdict")
                        result.update(verdict=verdict, explanation=str(prediction.get("explanation", "")))
                    except Exception as exc:
                        result.update(verdict=None, explanation="", error=type(exc).__name__)
                    result["correct"] = not result.get("error") and result["verdict"] == LABELS[row["label"]]
                    rows.append(result)
                    self.state["results"][key].update(score_rows(rows, len(selected)))
                    self.state.update(calls=llm.calls, tokens=llm.tokens)
                    self.save()
            await asyncio.gather(*(one(row) for row in selected))
            self.event(f"{key.replace('_', ' ').title()}: {self.state['results'][key]['correct']}/{len(selected)} correct.")
            return self.state["results"][key]

        try:
            async with asyncio.timeout(1200):
                baseline = playbook["prompts"]["judge"]
                dev = await evaluate(baseline, "dev", "baseline_dev")
                failures = [r for r in dev["rows"] if not r["correct"] and not r.get("error")]
                candidate = None
                if failures:
                    self.state["stage"] = "improve"
                    self.event("Improver is reviewing development mistakes only. Test claims remain held out.")
                    dev_by_id = {r["id"]: r for r in items if r["split"] == "dev"}
                    examples = [{**r, "evidence": dev_by_id[r["id"]]["evidence"]} for r in failures]
                    instruction = (
                        "Improve only the judge system prompt for evidence-based fact checking. Diagnose general "
                        "errors in the development examples. Never mention or memorize specific claims, entities, "
                        "or answers. Preserve the original JSON output schema and verdict vocabulary. Gold label "
                        "mapping: SUPPORTS=true, REFUTES=false, NOT ENOUGH INFO=unverifiable. Misleading is not "
                        "a match for REFUTES. Return JSON only: {\"diagnosis\": str, \"new_prompt\": str}.")
                    async with asyncio.timeout(150):
                        out = await llm.json("improver", instruction,
                                             json.dumps({"current_prompt": baseline, "dev_failures": examples}), strong=True)
                    candidate = out.get("new_prompt")
                    if not isinstance(candidate, str) or not candidate.strip():
                        raise ValueError("Improver did not return a usable prompt")
                    self.state.update(candidate_prompt=candidate, diagnosis=out.get("diagnosis", ""))
                    self.event("Candidate prompt frozen. No test labels were used to write it.")
                    await evaluate(candidate, "dev", "candidate_dev")
                else:
                    self.state["diagnosis"] = "No usable development mistakes to learn from. No candidate was created."
                    self.event(self.state["diagnosis"])
                await evaluate(baseline, "test", "baseline_test")
                if candidate:
                    await evaluate(candidate, "test", "candidate_test")
                    results = self.state["results"]
                    delta = results["candidate_test"]["correct"] - results["baseline_test"]["correct"]
                    dev_delta = results["candidate_dev"]["correct"] - results["baseline_dev"]["correct"]
                    errors = sum(r["errors"] for r in results.values())
                    self.state["outcome"] = ("Inconclusive: API errors affected the comparison." if errors else
                        f"Held-out change: {delta:+d} correct claims; development change: {dev_delta:+d}. "
                        "Candidate saved for review; app prompt unchanged.")
                else:
                    self.state["outcome"] = ("Inconclusive: API or output errors affected the baseline. "
                        if any(r["errors"] for r in self.state["results"].values()) else "Baseline measured. ") + \
                        "No candidate created; app prompt unchanged."
                self.state.update(status="complete", ended=time.time(), calls=llm.calls, tokens=llm.tokens)
                self.event(self.state["outcome"])
        except asyncio.CancelledError:
            self.state.update(status="stopped", ended=time.time())
            self.event("Stopped by user. Partial results retained.")
            raise
        except Exception as exc:
            self.state.update(status="failed", ended=time.time(), error=type(exc).__name__)
            self.event(f"Experiment stopped: {type(exc).__name__}. Partial results retained.")
        finally:
            self.state.update(calls=llm.calls, tokens=llm.tokens)
            self.save()
            if llm.client:
                await llm.client.close()
