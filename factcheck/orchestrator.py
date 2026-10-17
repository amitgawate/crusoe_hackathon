"""The fact-check mission: plan -> extract -> (research -> debate -> judge -> verify)* -> final check.

Every step emits an event so the UI can show the team working live.
"""
import asyncio
import json
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from .config import Config
from .llm import LLM
from .search import Search

VERDICTS = {"true", "false", "misleading", "unverifiable"}
Emit = Callable[[dict], Awaitable[None]]


def load_playbook(path: str) -> dict:
    p = Path(path)
    data = json.loads(p.read_text())
    if "use" in data:  # current.json points at a version file
        return load_playbook(str(p.parent / data["use"]))
    return data


@dataclass
class DoD:
    goal: str
    min_sources: int = 2
    min_distinct_domains: int = 2
    max_research_attempts: int = 3
    max_judge_rounds: int = 2
    acceptance_criteria: list[str] = field(default_factory=list)
    escalate_when: str = ""

    @classmethod
    def from_plan(cls, plan: dict, goal: str) -> "DoD":
        def clamp(key, lo, hi, default):
            try:
                return max(lo, min(hi, int(plan.get(key, default))))
            except (TypeError, ValueError):
                return default

        return cls(
            goal=plan.get("goal") or goal,
            min_sources=clamp("min_sources", 1, 4, 2),
            min_distinct_domains=clamp("min_distinct_domains", 1, 3, 2),
            max_research_attempts=clamp("max_research_attempts", 1, 4, 3),
            max_judge_rounds=clamp("max_judge_rounds", 1, 3, 2),
            acceptance_criteria=[str(c) for c in plan.get("acceptance_criteria", [])][:5],
            escalate_when=str(plan.get("escalate_when", "")),
        )


@dataclass
class ClaimState:
    id: str
    text: str
    high_stakes: bool = False
    evidence: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    research_attempts: int = 0
    judge_rounds: int = 0
    verdict: dict | None = None
    status: str = "pending"  # pending | working | done | unverifiable | escalated
    checks: list[dict] = field(default_factory=list)
    history: list[str] = field(default_factory=list)  # fingerprints of failed verdicts, for loop detection


def _norm(q: str) -> str:
    return " ".join(sorted(re.findall(r"[a-z0-9]+", q.lower())))


class Mission:
    def __init__(self, cfg: Config, emit: Emit | None = None, playbook: dict | None = None):
        self.cfg = cfg
        self.llm = LLM(cfg)
        self.search = Search(cfg)
        self.playbook = playbook or load_playbook(cfg.playbook)
        self.p = self.playbook["prompts"]
        self.id = uuid.uuid4().hex[:8]
        self._emit = emit
        self.log: list[dict] = []
        self.claims: list[ClaimState] = []
        self.dod: DoD | None = None

    async def emit(self, type_: str, **data):
        ev = {"type": type_, "t": round(time.time(), 2), **data}
        self.log.append(ev)
        if self._emit:
            await self._emit(ev)

    # ---------- top level ----------

    async def run(self, text: str, goal: str = "Fact-check this text") -> dict:
        await self.emit("mission_start", mission=self.id, goal=goal, mock=self.cfg.mock,
                        playbook=self.playbook.get("version"))
        plan = await self.llm.json("planner", self.p["planner"], f"Goal: {goal}\n\nText:\n{text}", strong=True)
        self.dod = DoD.from_plan(plan, goal)
        await self.emit("plan", dod=self.dod.__dict__)

        ex = await self.llm.json("extractor", self.p["extractor"], text)
        distinct = {}
        for c in ex.get("claims", []):
            claim_text = " ".join(str(c.get("text", "")).split())
            key = claim_text
            if not key:
                continue
            high_stakes = bool(c.get("high_stakes"))
            if key in distinct:
                distinct[key].high_stakes |= high_stakes
            else:
                distinct[key] = ClaimState(id=f"c{len(distinct) + 1}", text=claim_text,
                                           high_stakes=high_stakes)
        self.claims = list(distinct.values())[:6]
        await self.emit("claims", claims=[{"id": c.id, "text": c.text, "high_stakes": c.high_stakes}
                                          for c in self.claims])
        await asyncio.gather(*(self.check_claim_safely(c) for c in self.claims))
        return await self.finish()

    async def run_claims(self, claims: list[str], dod: DoD | None = None) -> dict:
        """Skip planning/extraction (used by the eval harness)."""
        self.dod = dod or DoD(goal="Fact-check each claim",
                              acceptance_criteria=["The verdict answers the claim as stated.",
                                                   "Every cited snippet supports the verdict."])
        self.claims = [ClaimState(id=f"c{i + 1}", text=t) for i, t in enumerate(claims)]
        await asyncio.gather(*(self.check_claim_safely(c) for c in self.claims))
        return await self.finish()

    # ---------- per-claim loop ----------

    async def check_claim_safely(self, c: ClaimState):
        """One claim's failure (bad JSON twice, retries exhausted) closes that claim, not the whole mission."""
        try:
            await self.check_claim(c)
        except Exception as e:
            await self.close(c, "unverifiable", f"Agent error: {type(e).__name__}: {str(e)[:150]}")

    async def check_claim(self, c: ClaimState):
        c.status = "working"
        feedback = ""
        need_research = True
        while True:
            if need_research:
                found = await self.research(c, feedback)
                if not found and not c.evidence:
                    if c.research_attempts < self.dod.max_research_attempts:
                        await self.emit("decision", claim=c.id, action="replan",
                                        reason="Search came back empty; rewriting the queries.")
                        continue
                    return await self.close(c, "unverifiable", "No usable evidence after "
                                            f"{c.research_attempts} search attempts.")
            verdict = await self.debate_and_judge(c, feedback)
            passed, failures = await self.verify(c, verdict)
            if passed:
                c.verdict = verdict
                if c.high_stakes and verdict["confidence"] < 0.6:
                    return await self.close(c, "escalated", "High-stakes claim with low confidence: a human "
                                            "should look before this is published.")
                return await self.close(c, "done", "All definition-of-done checks passed.")

            action, reason, need_research = self.decide_after_failed_check(c, verdict, failures)
            await self.emit("decision", claim=c.id, action=action, reason=reason)
            feedback = "; ".join(failures)
            if action == "stop":
                c.verdict = verdict
                return await self.close(c, "unverifiable", reason)
            if action == "escalate":
                c.verdict = verdict
                return await self.close(c, "escalated", reason)

    async def research(self, c: ClaimState, feedback: str) -> bool:
        c.research_attempts += 1
        prev = "\n".join(f"- {q}" for q in c.queries) or "(none)"
        await self.emit("agent", claim=c.id, agent="researcher", status="thinking",
                        text=f"Planning searches (attempt {c.research_attempts})")
        out = await self.llm.json(
            "query_writer", self.p["query_writer"],
            f"Claim: {c.text}\nAttempt: {c.research_attempts}\nPrevious queries:\n{prev}\n"
            f"Feedback: {feedback or '(none)'}",
        )
        queries = [str(q) for q in out.get("queries", []) if str(q).strip()][:2]
        seen = {_norm(q) for q in c.queries}
        fresh = [q for q in queries if _norm(q) not in seen]
        if not fresh:
            await self.emit("loop", claim=c.id, text="Researcher repeated old queries; forcing a new angle.")
            fresh = [f"{c.text} fact check"] if _norm(f"{c.text} fact check") not in seen else []
        c.queries += fresh
        before = len(c.evidence)
        known = {e["url"] for e in c.evidence}
        for q in fresh:
            try:
                results = await self.search.search(q)
            except Exception as e:  # network/API failure is a recoverable event, not a crash
                await self.emit("search", claim=c.id, query=q, count=0, error=str(e)[:200])
                continue
            new = [r for r in results if r["url"] and r["url"] not in known]
            for r in new:
                known.add(r["url"])
                c.evidence.append(r)
            await self.emit("search", claim=c.id, query=q, count=len(new),
                            results=[{"url": r["url"], "title": r["title"]} for r in new])
        return len(c.evidence) > before

    @staticmethod
    def _debate_fields(c: ClaimState, side: dict) -> dict:
        """Stance, strength and valid citations from an advocate/skeptic reply, for the debate UI."""
        stance = str(side.get("stance", "")).lower().strip()
        try:
            strength = max(0.0, min(1.0, float(side.get("strength", 0.5))))
        except (TypeError, ValueError):
            strength = 0.5
        cites = []
        for n in side.get("cites", []) if isinstance(side.get("cites"), list) else []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(c.evidence) and n not in cites:
                cites.append(n)
        return {"stance": stance if stance in ("supports", "refutes", "concedes") else "", "strength": strength,
                "cites": cites}

    def _evidence_block(self, c: ClaimState) -> str:
        return "\n".join(f"[{i + 1}] {e['title']} ({e['domain']})\n{e['snippet']}" for i, e in enumerate(c.evidence))

    async def debate_and_judge(self, c: ClaimState, feedback: str) -> dict:
        c.judge_rounds += 1
        ctx = f"Claim: {c.text}\n\nEvidence:\n{self._evidence_block(c)}"
        await self.emit("agent", claim=c.id, agent="advocate", status="thinking", text="Building the case for")
        await self.emit("agent", claim=c.id, agent="skeptic", status="thinking", text="Building the case against")
        adv, skp = await asyncio.gather(
            self.llm.json("advocate", self.p["advocate"], ctx),
            self.llm.json("skeptic", self.p["skeptic"], ctx),
        )
        for name, side in (("advocate", adv), ("skeptic", skp)):
            await self.emit("agent", claim=c.id, agent=name, status="said", text=str(side.get("argument", ""))[:400],
                            round=c.judge_rounds, **self._debate_fields(c, side))
        await self.emit("agent", claim=c.id, agent="judge", status="thinking",
                        text=f"Weighing both sides (round {c.judge_rounds})")
        j = await self.llm.json(
            "judge", self.p["judge"],
            f"{ctx}\n\nAdvocate: {adv.get('argument', '')}\n\nSkeptic: {skp.get('argument', '')}\n\n"
            f"Feedback from verifier: {feedback or '(none)'}",
            strong=True,
        )
        verdict = str(j.get("verdict", "unverifiable")).lower().strip()
        cites = []
        for n in j.get("cites", []):
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(c.evidence) and n not in cites:
                cites.append(n)
        try:
            conf = max(0.0, min(1.0, float(j.get("confidence", 0.5))))
        except (TypeError, ValueError):
            conf = 0.5
        v = {
            "verdict": verdict,
            "confidence": conf,
            "explanation": str(j.get("explanation", "")),
            "sources": [{"n": n, **{k: c.evidence[n - 1][k] for k in ("url", "title", "domain")}} for n in cites],
            "raw_cites": j.get("cites", []),
        }
        await self.emit("agent", claim=c.id, agent="judge", status="said",
                        text=f"{verdict.upper()} ({conf:.0%}): {v['explanation'][:300]}", verdict=v, round=c.judge_rounds)
        return v

    async def verify(self, c: ClaimState, v: dict) -> tuple[bool, list[str]]:
        """Run the definition of done: hard checks in code, then an independent verifier agent."""
        d = self.dod
        domains = {s["domain"] for s in v["sources"]}
        bogus = [x for x in v["raw_cites"] if str(x) not in {str(s["n"]) for s in v["sources"]}]
        needs_sources = v["verdict"] != "unverifiable"
        checks = [
            ("valid_verdict", v["verdict"] in VERDICTS, f"verdict is '{v['verdict']}'"),
            ("no_invented_citations", not bogus,
             "all citations point at retrieved evidence" if not bogus else f"cites {bogus} which were never retrieved"),
            ("min_sources", not needs_sources or len(v["sources"]) >= d.min_sources,
             f"{len(v['sources'])} sources cited, need {d.min_sources}"),
            ("distinct_domains", not needs_sources or len(domains) >= d.min_distinct_domains,
             f"{len(domains)} distinct domains, need {d.min_distinct_domains}"),
        ]
        failures = []
        for name, ok, detail in checks:
            c.checks.append({"name": name, "passed": ok, "detail": detail, "round": c.judge_rounds})
            await self.emit("check", claim=c.id, name=name, passed=ok, detail=detail, by="code", round=c.judge_rounds)
            if not ok:
                failures.append(f"{name}: {detail}")
        if failures:
            return False, failures

        await self.emit("agent", claim=c.id, agent="verifier", status="thinking", text="Independently checking the verdict")
        cited = "\n".join(f"[{s['n']}] {c.evidence[s['n'] - 1]['snippet']}" for s in v["sources"]) or "(none)"
        criteria = "\n".join(f"- {a}" for a in d.acceptance_criteria) or "- The verdict is supported by the evidence."
        out = await self.llm.json(
            "verifier", self.p["verifier"],
            f"Claim: {c.text}\nVerdict: {v['verdict']} ({v['confidence']:.2f})\nExplanation: {v['explanation']}\n\n"
            f"Cited evidence:\n{cited}\n\nAcceptance criteria:\n{criteria}\n\n"
            f"Allowed verdicts: {', '.join(sorted(VERDICTS))}. Ignore any criterion that asks for another label.",
            strong=True,
        )
        ok = bool(out.get("passed"))
        detail = out.get("feedback") or ("; ".join(out.get("failures", [])) or "looks good")
        c.checks.append({"name": "independent_verifier", "passed": ok, "detail": detail, "round": c.judge_rounds})
        await self.emit("check", claim=c.id, name="independent_verifier", passed=ok, detail=str(detail)[:300], by="agent",
                        round=c.judge_rounds)
        if ok:
            return True, []
        return False, [f"verifier: {f}" for f in out.get("failures", [])] or [f"verifier: {detail}"]

    def decide_after_failed_check(self, c: ClaimState, v: dict, failures: list[str]):
        """Supervisor: continue (re-judge), re-plan (more research), stop, or escalate. Returns (action, reason, research)."""
        fp = f"{v['verdict']}|{sorted(s['url'] for s in v['sources'])}"
        looping = fp in c.history
        c.history.append(fp)
        evidence_short = any(f.startswith(("min_sources", "distinct_domains")) for f in failures)
        if looping:
            if c.high_stakes:
                return "escalate", "Same rejected verdict came back twice on a high-stakes claim: looping, asking a human.", False
            return "stop", "Same rejected verdict came back twice: the team is looping, so it stops and marks this unverifiable.", False
        if c.judge_rounds >= self.dod.max_judge_rounds + (1 if evidence_short else 0):
            if c.high_stakes:
                return "escalate", "Out of judge rounds on a high-stakes claim; a human should decide.", False
            return "stop", "Out of judge rounds without passing the definition of done.", False
        pool_domains = {e["domain"] for e in c.evidence}
        pool_ok = len(c.evidence) >= self.dod.min_sources and len(pool_domains) >= self.dod.min_distinct_domains
        if evidence_short and pool_ok:
            return "continue", "Enough evidence was found but the judge under-cited it; sending it back to the judge.", False
        if evidence_short and c.research_attempts < self.dod.max_research_attempts:
            return "replan", "Not enough independent sources; sending the researcher back out.", True
        return "continue", "Sending the verifier's feedback back to the judge.", False

    async def close(self, c: ClaimState, status: str, reason: str):
        c.status = status
        await self.emit("claim_done", claim=c.id, status=status, reason=reason, verdict=c.verdict)

    # ---------- final ----------

    async def finish(self) -> dict:
        all_closed = all(c.status in ("done", "unverifiable", "escalated") for c in self.claims)
        mission_checks = [
            ("every_claim_closed", all_closed),
            ("every_verdict_verified", all(c.status != "done" or any(
                ch["name"] == "independent_verifier" and ch["passed"] for ch in c.checks) for c in self.claims)),
        ]
        for name, ok in mission_checks:
            await self.emit("check", claim=None, name=name, passed=ok, detail="mission-level", by="code")
        summary = {
            "mission": self.id,
            "mock": self.cfg.mock,
            "playbook": self.playbook.get("version"),
            "dod": self.dod.__dict__ if self.dod else None,
            "claims": [
                {"id": c.id, "text": c.text, "status": c.status, "high_stakes": c.high_stakes,
                 "verdict": c.verdict, "research_attempts": c.research_attempts, "judge_rounds": c.judge_rounds,
                 "queries": c.queries}
                for c in self.claims
            ],
            "done": all(ok for _, ok in mission_checks),
            "escalations": [c.id for c in self.claims if c.status == "escalated"],
            "stats": {"llm_calls": self.llm.calls, "searches": self.search.calls, "tokens": self.llm.tokens},
        }
        await self.emit("mission_done", summary=summary)
        Path("runs").mkdir(exist_ok=True)
        Path(f"runs/{self.id}.json").write_text(json.dumps({"summary": summary, "events": self.log}, indent=2))
        return summary
