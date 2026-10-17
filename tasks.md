# Local setup

## Article URL input

Contract: a public article URL downloads into the visible text field before the existing live run begins; failed downloads never start a mission. Keep manual pasted text working.

- [x] Add endpoint tests for extracted content, invalid/private URLs, redirects, empty pages and download errors.
- [x] Implement bounded HTML download and article extraction; connect Download & run to the existing UI flow.
- [x] Verify 25 tests, real EIA extraction (543 words), and the browser flow on a separate mock server. A blocked URL leaves the mission unstarted; a valid URL displays text and automatically completes the mock workflow.

Live port 8000 was found stopped and restored as a detached process; HTTP 200 and live mode confirmed. URL requests connect directly to validated public IPs and preserve Host/TLS hostname verification, with fresh connections across redirects. Blocked/paywalled/JavaScript-only pages may still require manual pasting.

The final no-keepalive setting for redirect connection isolation was saved after the live server started. The user's active connection on port 8000 was preserved; that setting loads on the next restart. The URL feature itself is already available on the live server. The separate mock test server was stopped after browser verification.

Files: `factcheck/articles.py`, `server.py`, `static/index.html`, `requirements.txt`, `tests/test_articles.py`. Use Trafilatura for article extraction; preserve title, source and publication date when available. No commits or pushes.

- [x] Clone repo and inspect startup/configuration.
- [x] Install dependencies into .venv; verify imports and existing tests.
- [x] Start on localhost:8000; verify page, config, and a complete offline mission.
- [x] Confirm local env key readiness without exposing secrets.

Verification: 4 tests passed; HTTP 200 for the UI; offline mission completed with 125 streamed events and no errors.

Live setup verified: both keys are present in the ignored .env; the server was restarted and /api/config reports mock=false. Crusoe authentication returned HTTP 200, both configured models returned real text, and You.com returned two search results. A complete live mission has not been tested.

## Full-article live trial

User revised scope: finish the current solar article, then stop the batch. Keep all five article files for manual review. The batch client has been stopped; the already-running solar mission continues on the local server.

- [x] Collect five complete, reusable news articles with dates and source URLs; verify extracted bodies.
- [x] Submit the full solar article to the live /api/run endpoint; retain its stream and preserve the earlier failed attempt.
- [x] Confirm the solar mission's terminal result and stop the batch: 20 claims in 647 seconds; app labels were 9 true, 4 false and 7 unresolved. Accepted labels include reasoning errors requiring human review.
- [x] Deliver article files and RESULTS.txt with claims, citations, and limitations.

Contract: run the app on the actual article text after the approved dynamic-claim update, retain source provenance, and report observed results without treating workflow completion as proof of truth.

## Dynamic claim coverage

Approved behavior: check every distinct factual claim; longer articles may take longer.
Invariant: the pipeline must retain every nonempty distinct claim returned by extraction, with no fixed claim-count cutoff.

Files: `playbooks/v1.json` (extractor instructions), `factcheck/orchestrator.py` (retain and deduplicate extracted claims), `factcheck/mock.py` (offline parity), `tests/test_mission.py` (coverage regressions).

- [x] Write regression tests for eight claims, repeated claims, and one-claim input; confirmed two failures for the count cap and duplicate processing.
- [x] Remove live and mock count caps, ask the extractor for all distinct claims, and merge textual duplicates without losing high-stakes flags.
- [x] Run targeted mission and LLM tests (11 passed), review the diff, restart the local app, and verify live extraction (31 claims from the full battery article).

Scope: preserve existing LLM concurrency and retry budgets. No new flag, dependency, commit, or push. The LLM can still omit claims or hit its output-token limit; this change removes the arbitrary six-claim cutoff, not the need to evaluate extraction coverage.

Review finding: case-insensitive deduplication merged 5 mW with 5 MW. Added a failing regression test and changed deduplication to preserve case and punctuation. Review also confirmed no additional count limits in the UI, CLI, or summary.

Live extraction diagnosis: the default Nemotron request returned finish_reason=length, 4,096 completion tokens and empty content. An otherwise identical request with thinking disabled returned 31 claims in 12 seconds, including the final paragraph. Added a failing LLM-boundary regression, then disabled thinking only for Nemotron extraction; judging and verification keep their existing behavior. Official NVIDIA reasoning-mode documentation checked before the SDK change.

The first full-article run exposed the same exhausted-token failure in a downstream agent after 274 seconds. Invariant for the retry fix: never accept truncated output, and do not repeat an exhausted Nemotron reasoning budget unchanged. Added failing tests for empty and partial length-limited replies, then made those retries disable thinking. Normal first attempts retain reasoning. Preserved initial trial logs before rerunning all five articles.

## Six-claim limit (supersedes dynamic coverage)

Contract: article runs check at most six distinct factual claims, with no padding for short articles. Both pasted text and downloaded articles use this limit. Leave the local app stopped as requested.

- [x] Stop the current server.
- [x] Verified regression failed with eight claims before applying the cap.
- [x] Applied prompt/backend cap and UI explanation; 14 targeted mission/LLM tests passed, git diff --check passed, and port 8000 has no listener.

## Fast FEVER improvement experiment

Contract: score a fixed, balanced external FEVER sample with supplied evidence, hide gold labels from prediction, expose only development failures to the improver, and evaluate a frozen candidate on held-out claims. This is judge-only label accuracy, not full-pipeline FEVER score. Never activate a candidate automatically.

- [x] Download evidence-bearing FEVER rows and freeze 15 dev / 15 test claims without duplicate claim IDs or shared evidence pages across splits.
- [x] Test strict label scoring, label-free judge inputs, and split isolation; implement an isolated fast runner with per-call timeouts and saved progress.
- [x] Run baseline, one prompt revision from dev failures, candidate dev, then held-out comparison. Save actual results and a concise report; do not commit, push, or start the app.

Dashboard: `/improvement`, linked from the new debate UI on main f4a9569. Live charts, stage progression, claim comparison, stop/export, and persistent snapshots are connected to the runner. 35 targeted tests passed before the live run.

Review corrections: match the app's normalization for boolean verdicts, but reject missing/unknown verdicts instead of crediting NEI; only Crusoe credentials are needed for this judge-only runner; decode FEVER title escapes in evidence links. First live attempt fb24608cfd56 is explicitly invalidated and retained because its evaluator counted boolean false incorrectly. Fresh experiment c7f7f85a2392 uses the corrected scorer. No dataset or prompt changes were selected using held-out results.

Completed c7f7f85a2392 in 238.3 seconds: development 14/15 → 15/15, held out 11/15 → 14/15; zero API errors. Candidate remains inactive; report and candidate playbook saved in eval/results/fever/. Final targeted suite: 37 passed.

## Demo replay and prompt diff

Contract: replay only saved verdicts, clearly label paced playback, never invoke inference or alter saved results. Show exact prompt additions/removals, preserve unchanged text, and make real score/stage updates visibly animate with reduced-motion support.

- [x] Test prompt reconstruction and replay isolation, intermediate scores, and final-result fidelity.
- [x] Add full-width word diff, replay/pause/speed/scrub controls, animated stage flow, and cumulative accuracy trace.
- [x] Verify the live page, replay controls and final scores, and confirm no new experiment or API calls were created.

Validation: 14 targeted UI/benchmark tests passed. Browser checks covered pause, stepping, seeking, speed, automatic completion and return to results; no console errors. The saved run checksum remained unchanged after replay. Review also caught and fixed control recovery after failed starts and reconnection with unchanged snapshots.
