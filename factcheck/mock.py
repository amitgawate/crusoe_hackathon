"""Offline stand-ins for Crusoe and You.com so the demo and tests run without keys or network.

The canned behaviour is chosen to exercise every branch of the loop: an empty first search,
a judge that under-cites on its first round, and a high-stakes claim that gets escalated.
"""
import json
import re
import zlib

DOMAINS = ["britannica.com", "nasa.gov", "nationalgeographic.com", "scientificamerican.com",
           "snopes.com", "reuters.com", "apnews.com", "nih.gov", "who.int", "smithsonianmag.com"]

# keyword -> (verdict, what the "sources" say)
KNOWN = [
    ("great wall", "false", "Astronauts report the Great Wall is not visible to the naked eye from the Moon."),
    ("10%", "false", "Brain imaging shows virtually all of the brain is active; the 10% figure is a myth."),
    ("10 percent", "false", "Brain imaging shows virtually all of the brain is active; the 10% figure is a myth."),
    ("lightning", "false", "Lightning often strikes the same place repeatedly; the Empire State Building is hit ~20 times a year."),
    ("eiffel", "true", "The Eiffel Tower was completed in 1889 for the Exposition Universelle."),
    ("boil", "true", "At standard sea-level pressure, pure water boils at 100 degrees Celsius."),
    ("goldfish", "false", "Studies show goldfish can remember things for months, not three seconds."),
    ("vitamin c", "misleading", "Reviews find vitamin C does not prevent colds for most people, though it may shorten them slightly."),
    ("mount everest", "true", "Mount Everest is Earth's highest mountain above sea level at about 8,849 m."),
]
HIGH_STAKES = ("vitamin", "vaccine", "cure", "cancer", "election", "invest", "covid")


def _h(s: str) -> int:
    return zlib.crc32(s.encode())


def _field(text: str, name: str) -> str:
    m = re.search(rf"^{name}:\s*(.*)$", text, flags=re.M)
    return m.group(1).strip() if m else ""


def _lookup(claim: str):
    low = claim.lower()
    for key, verdict, fact in KNOWN:
        if key in low:
            return verdict, fact
    return "true", f"Multiple reference works confirm that {claim.rstrip('.')}."


def llm(role: str, system: str, user: str) -> str:
    claim = _field(user, "Claim")
    if role == "planner":
        out = {
            "goal": "Give every factual claim in the text a sourced verdict a reader can trust",
            "min_sources": 2,
            "min_distinct_domains": 2,
            "max_research_attempts": 3,
            "max_judge_rounds": 2,
            "acceptance_criteria": [
                "The verdict answers the claim exactly as stated, not a nearby claim.",
                "Every cited snippet directly supports the verdict.",
                "At least two independent outlets agree.",
                "The explanation names what the evidence shows.",
            ],
            "escalate_when": "A health, safety or money claim stays low-confidence or contested.",
        }
    elif role == "extractor":
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", user) if len(s.split()) >= 4]
        out = {"claims": [{"text": s, "high_stakes": any(k in s.lower() for k in HIGH_STAKES)}
                          for s in sentences[:6]]}
    elif role == "query_writer":
        attempt = int(_field(user, "Attempt") or 1)
        if attempt == 1 and re.search(r"\d", claim):
            out = {"queries": [" ".join(claim.split()[:2])]}  # too vague: mock search returns nothing
        else:
            out = {"queries": [claim, f"is it true that {claim.rstrip('.').lower()}"]}
    elif role in ("advocate", "skeptic"):
        verdict, fact = _lookup(claim)
        agrees = (verdict == "true") == (role == "advocate")
        out = {"argument": (f"The evidence backs this side: {fact}" if agrees else
                            f"The evidence does not really support this side. {fact}"),
               "cites": [1, 2]}
    elif role == "judge":
        verdict, fact = _lookup(claim)
        first_round = "Feedback from verifier: (none)" in user
        lazy = first_round and _h(claim) % 2 == 0
        conf = 0.45 if any(k in claim.lower() for k in HIGH_STAKES) else 0.9
        out = {"verdict": verdict, "confidence": conf, "explanation": fact,
               "cites": [1] if lazy else [1, 2, 3]}
    elif role == "verifier":
        out = {"passed": True, "failures": [], "feedback": "Cited snippets support the verdict and match the claim."}
    elif role == "improver":
        out = {"diagnosis": "Mock mode: no real failures to learn from.", "changes": {}}
    else:
        out = {}
    return json.dumps(out)


def search(query: str) -> list[dict]:
    if len(query.split()) < 3:
        return []
    verdict, fact = _lookup(re.sub(r"^is it true that ", "", query, flags=re.I))
    start = _h(query) % len(DOMAINS)
    results = []
    for i in range(3):
        d = DOMAINS[(start + i * 3) % len(DOMAINS)]
        slug = re.sub(r"[^a-z0-9]+", "-", query.lower()).strip("-")[:50]
        results.append({
            "url": f"https://www.{d}/{slug}",
            "title": f"{query[:60]} | {d.split('.')[0].title()}",
            "description": fact,
            "snippets": [fact],
        })
    return results
