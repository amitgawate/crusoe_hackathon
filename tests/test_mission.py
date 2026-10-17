import asyncio
import os

os.environ["FC_MOCK"] = "1"

from factcheck.cli import SAMPLE_TEXT  # noqa: E402
from factcheck.config import Config  # noqa: E402
from factcheck.llm import parse_json  # noqa: E402
from factcheck.orchestrator import ClaimState, DoD, Mission  # noqa: E402


def run(text):
    events = []

    async def emit(ev):
        events.append(ev)

    summary = asyncio.run(Mission(Config(), emit).run(text))
    return summary, events


def test_sample_mission_meets_definition_of_done():
    summary, events = run(SAMPLE_TEXT)
    assert summary["done"]
    statuses = {c["text"]: c["status"] for c in summary["claims"]}
    assert len(statuses) == 5
    # the vitamin C claim is high stakes and low confidence, so it goes to a human
    assert summary["escalations"] == ["c5"]
    verdicts = {c["id"]: c["verdict"]["verdict"] for c in summary["claims"]}
    assert verdicts["c1"] == "true" and verdicts["c2"] == "false"


def test_team_recovers_and_self_corrects():
    _, events = run(SAMPLE_TEXT)
    decisions = [e["action"] for e in events if e["type"] == "decision"]
    assert "replan" in decisions  # empty search -> rewritten queries
    assert "continue" in decisions  # under-cited verdict -> sent back to the judge
    failed = [e for e in events if e["type"] == "check" and not e["passed"]]
    assert failed, "the definition of done should catch at least one bad verdict"


def test_supervisor_stops_on_repeated_verdict():
    m = Mission(Config())
    m.dod = DoD(goal="x")
    c = ClaimState(id="c1", text="claim", judge_rounds=1)
    v = {"verdict": "true", "sources": [{"url": "https://a.com/x"}]}
    assert m.decide_after_failed_check(c, v, ["verifier: nope"])[0] == "continue"
    assert m.decide_after_failed_check(c, v, ["verifier: nope"])[0] == "stop"
    c2 = ClaimState(id="c2", text="claim", judge_rounds=1, high_stakes=True)
    m.decide_after_failed_check(c2, v, ["verifier: nope"])
    assert m.decide_after_failed_check(c2, v, ["verifier: nope"])[0] == "escalate"


def test_parse_json_handles_fences_and_thinking():
    assert parse_json('<think>hmm {"a": 0}</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Sure! {"b": [1, 2]} hope that helps') == {"b": [1, 2]}


def test_one_failing_claim_does_not_abort_the_mission():
    m = Mission(Config())
    real = m.check_claim

    async def flaky(c):
        if c.id == "c2":
            raise ValueError("Expecting ',' delimiter")
        await real(c)

    m.check_claim = flaky
    summary = asyncio.run(m.run_claims(["The Eiffel Tower was completed in 1889.", "Some claim", "Another claim"]))
    statuses = {c["id"]: c["status"] for c in summary["claims"]}
    assert statuses["c2"] == "unverifiable"
    assert statuses["c1"] != "unverifiable" or statuses["c3"] != "unverifiable"
    assert summary["done"]  # every claim still closed
