# Done Means Done: a fact-checking agent team you can walk away from

Built for the Crusoe Recursive Agents Hackathon, **Done Means Done** track (with a **Version N+1** extension).

You give it a one-line goal ("fact-check this before we publish") and some text. A team of agents running on
**NVIDIA Nemotron via Crusoe Managed Inference**, with **You.com search** as its only source of facts:

1. **Planner** turns the vague goal into a *definition of done*: how many sources each verdict needs, how many
   distinct outlets, retry budgets, 3 to 5 acceptance criteria, and when to ask a human.
2. **Extractor** pulls out the checkable claims and flags high-stakes ones (health, safety, money, elections).
3. For each claim, in parallel:
   - **Researcher** writes queries and searches You.com. If a search comes back empty, it re-plans with a new angle.
   - **Advocate** and **Skeptic** argue for and against using only the retrieved evidence.
   - **Judge** (strong model) gives a verdict with numbered citations.
   - **Definition-of-done checks** run in code: a valid verdict, no invented citations, enough sources, enough
     distinct domains. Then an **independent verifier** agent checks the acceptance criteria and drift.
   - **Supervisor** reads failures and decides: *continue* (send feedback to the judge), *re-plan* (more research),
     *stop* (mark unverifiable), or *escalate* (rarely, ping a human). It catches loops: repeated queries, or the
     same rejected verdict twice.
4. **Mission checks** confirm every claim is closed and every "done" verdict was independently verified.

Every step streams to a live UI, so you can watch the team plan, disagree, get caught by its own checks, and fix it.

## Version N+1: the team improves itself

`eval/claims.json` holds 24 labeled facts and myths, split into `dev` and a held-out `test` set.
`python -m eval.improve` scores the current playbook (all prompts live in `playbooks/vN.json`), lets an improver
agent read the dev failures and rewrite the prompts, re-scores, and **keeps the new version only if dev improves**.
It then reports the before and after score on the held-out test claims. History goes to `playbooks/history.jsonl`.

## Run it

```bash
pip install -r requirements.txt
export CRUSOE_API_KEY='...'   # single quotes: keys can contain $
export YDC_API_KEY='...'
uvicorn server:app --port 8000    # open http://localhost:8000
python -m factcheck.cli           # same mission in the terminal
python -m eval.run_eval --split dev
python -m eval.improve
pytest -q
```

Without keys (or with `FC_MOCK=1`) everything runs offline with canned responses designed to exercise each branch
(empty search, an under-cited verdict, an escalation). The UI badge shows which mode you're in.

### Models

| Role | Default model | Env override |
|---|---|---|
| Planner, judge, verifier, improver | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B` | `FC_MODEL_STRONG` |
| Extractor, researcher, advocate, skeptic | `nvidia/Nemotron-3.5-Lightning-30B-A3B` | `FC_MODEL_FAST` |

Confirm IDs with `curl -s https://api.inference.crusoecloud.com/v1/models -H "Authorization: Bearer $CRUSOE_API_KEY" | jq -r '.data[].id'`.
Accounts without a payment method get 30 requests per minute per model, so roles are split across two models and
concurrency is capped (`FC_CONCURRENCY`, default 4) with jittered backoff on 429 and 503 errors.

## 3-minute demo script

1. **(0:00)** "Agents fill vague goals with their own assumptions and then report success. Ours writes down what
   done means first." Click **Run the team** and point at the definition of done the planner just wrote.
2. **(0:40)** Claims fan out. Point at the **search came back empty, rewriting queries** recovery and the
   **advocate vs. skeptic** debate.
3. **(1:20)** Point at a red check: the judge cited one source, and the code check caught it. The supervisor sent it
   back, and round 2 passes. Then the **independent verifier** signs off.
4. **(2:00)** The vitamin C claim is high stakes and low confidence, so it's the one claim **escalated to a human**.
   Everything else closed on its own, and both mission checks are green.
5. **(2:30)** Version N+1: show `playbooks/history.jsonl` with the before and after held-out score.

## Layout

```
factcheck/orchestrator.py  mission loop, definition of done, supervisor
factcheck/llm.py           Crusoe client (OpenAI SDK), backoff, JSON repair
factcheck/search.py        You.com Search API client
factcheck/mock.py          offline stand-ins
playbooks/                 versioned prompts (current.json points at the live one)
eval/                      labeled claims, scorer, self-improvement loop
server.py, static/         FastAPI + streaming UI
```
