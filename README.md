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
agent read the dev failures and rewrite **one prompt per round** (so every gain is attributable to a single prompt),
re-scores, and **keeps the new version only if dev improves**. It then reports the before and after score on the
held-out test claims. Each round appends one self-contained record to `playbooks/history.jsonl`: the changed prompt,
its text before and after, and the dev and test scores. `--rounds N` stops early when there are no dev failures left
or the improver proposes no change.

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

## Set up with Claude Code

Paste this prompt into Claude Code, started in the folder where you want the project. You need your own Crusoe and
You.com API keys. The prompt never asks Claude to handle the key values.

````
Set up the "Done Means Done" fact-check agent team from https://github.com/amitgawate/crusoe_hackathon and install the You.com plugin for this project.

1. Clone the repo and cd into it. If it is already cloned, run `git pull` instead.
2. Create .claude/settings.json in the repo with exactly this content:
{
  "extraKnownMarketplaces": {
    "you-com": {
      "source": { "source": "github", "repo": "youdotcom-oss/agent-skills" }
    }
  },
  "enabledPlugins": { "you@you-com": true }
}
3. Install the plugin at project scope:
   claude plugin marketplace add youdotcom-oss/agent-skills --scope project
   claude plugin install you@you-com --scope project
4. Create a venv: `python3 -m venv .venv`, then `.venv/bin/pip install -r requirements.txt`.
5. Copy .env.example to .env. Do not fill in keys yourself, and never print key values. Ask me to paste CRUSOE_API_KEY and YDC_API_KEY into .env myself, using single quotes.
6. Run the offline tests: `.venv/bin/python -m pytest -q`. Expect 4 passed.
7. After I confirm the keys are in .env, run the live mission: `.venv/bin/python -m factcheck.cli`. Report the RESULT block and the stats line.
8. Start the UI in the background: `.venv/bin/uvicorn server:app --port 8000`. Tell me to open http://localhost:8000.
9. Do not commit or push anything.

Notes:
- The code loads .env by itself. No `source` step is needed.
- The Crusoe model IDs are already the defaults: nvidia/NVIDIA-Nemotron-3-Super-120B-A12B and nvidia/Nemotron-3.5-Lightning-30B-A3B.
- Keep the default You.com URL (https://ydc-index.io/v1/search). The api.ydc-index.io address returns 403.
- When you finish, tell me to restart Claude Code, so the You.com skills load.
````

The plugin adds the `you-web`, `you-research`, `you-finance` and `you-discover` skills. It also blocks the built-in WebSearch and WebFetch tools in this folder and routes them to You.com search.

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
