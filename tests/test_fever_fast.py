import json
import pytest

from eval.fever_fast import judge_input, score_rows, select_sample


@pytest.fixture
def isolated_dashboard(tmp_path, monkeypatch):
    import server
    from eval.fever_fast import Experiment
    monkeypatch.setattr(server, 'experiment', Experiment(output=tmp_path / 'dashboard'))
    return server.app


def rows():
    return [{"id": str(i), "original_id": i, "claim": f"Claim {i}",
             "label": label, "evidence": [[f"Page_{i}", "0", f"Evidence {i}"]]}
            for i, label in enumerate(["SUPPORTS", "REFUTES", "NOT ENOUGH INFO"] * 20)]


def test_sample_balances_labels_and_keeps_related_pages_out_of_test():
    source = rows()
    source[1]["evidence"][0][0] = source[0]["evidence"][0][0]
    sample = select_sample(source, per_label=2)
    assert len(sample) == 12
    for split in ("dev", "test"):
        selected = [r for r in sample if r["split"] == split]
        assert sorted(r["label"] for r in selected) == sorted(["SUPPORTS", "REFUTES", "NOT ENOUGH INFO"] * 2)
    dev_pages = {e[0] for r in sample if r["split"] == "dev" for e in r["evidence"]}
    test_pages = {e[0] for r in sample if r["split"] == "test" for e in r["evidence"]}
    assert not dev_pages & test_pages
    assert len({r["original_id"] for r in sample}) == 12
    assert sample == select_sample(source, per_label=2)


def test_insufficient_sample_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        select_sample(rows()[:3], per_label=2)


def test_judge_cannot_see_gold_label_or_split():
    row = {**rows()[0], "label": "SECRET_GOLD", "split": "SECRET_SPLIT"}
    prompt = judge_input(row)
    assert "Claim 0" in prompt and "Evidence 0" in prompt
    assert "SECRET_GOLD" not in prompt and "SECRET_SPLIT" not in prompt


def test_scoring_counts_errors_and_does_not_equate_misleading_with_refuted():
    result = score_rows([
        {"label": "SUPPORTS", "verdict": "true"},
        {"label": "REFUTES", "verdict": "misleading"},
        {"label": "NOT ENOUGH INFO", "verdict": "unverifiable"},
        {"label": "REFUTES", "verdict": None, "error": "timeout"},
    ], total=4)
    assert result == {"correct": 2, "completed": 4, "total": 4, "accuracy": 50.0, "errors": 1}
    assert score_rows([], total=15)["accuracy"] is None


def test_dashboard_serves_real_dataset_with_empty_results(isolated_dashboard):
    from fastapi.testclient import TestClient
    from server import app
    client = TestClient(app)
    response = client.get('/api/improvement')
    assert response.status_code == 200
    state = response.json()
    assert state['dataset']['size'] == 30
    assert len(state['items']) == 30
    assert state['status'] == 'idle'
    assert state['results'] == {}
    assert client.get('/improvement').status_code == 200


def test_dashboard_rejects_mock_benchmark(monkeypatch, isolated_dashboard):
    from fastapi.testclient import TestClient
    from server import app
    monkeypatch.setenv('FC_MOCK', '1')
    response = TestClient(app).post('/api/improvement/start')
    assert response.status_code == 400
    assert 'mock' in response.json()['detail']


def test_experiment_records_progress_keeps_test_out_of_improver_and_does_not_activate(tmp_path, monkeypatch):
    import asyncio
    from factcheck.config import Config
    from factcheck.llm import LLM
    from eval.fever_fast import Experiment
    sample = select_sample(rows(), per_label=1)
    dataset = tmp_path / 'sample.json'
    dataset.write_text(json.dumps({'metadata': {'size': 6}, 'items': sample}))
    playbook = tmp_path / 'current.json'
    original = json.dumps({'version': 7, 'prompts': {'judge': 'Baseline prompt'}})
    playbook.write_text(original)
    captured = []

    async def predict(self, role, system, user, strong=False):
        self.calls += 1
        captured.append((role, user))
        if role == 'improver':
            return {'diagnosis': 'Distinguish missing evidence from contradiction.', 'new_prompt': 'Candidate prompt'}
        row = next(r for r in sample if user == judge_input(r))
        verdict = {'SUPPORTS': 'true', 'REFUTES': 'false', 'NOT ENOUGH INFO': 'unverifiable'}[row['label']]
        if system == 'Baseline prompt' and verdict == 'unverifiable':
            verdict = 'false'
        return {'verdict': False if verdict == 'false' else verdict, 'explanation': 'Test transport response'}

    monkeypatch.setattr(LLM, 'json', predict)
    exp = Experiment(dataset, tmp_path / 'results')

    async def run():
        exp.start(Config(mock=False, crusoe_api_key='test-key', playbook=str(playbook)))
        import pytest
        with pytest.raises(ValueError, match='already running'):
            exp.start()
        await exp.task
    asyncio.run(run())
    state = exp.snapshot()
    assert state['status'] == 'complete'
    assert state['results']['baseline_test']['correct'] == 2
    assert state['results']['candidate_test']['correct'] == 3
    assert playbook.read_text() == original
    assert Experiment(dataset, tmp_path / 'results').snapshot()['id'] == state['id']
    improvement_input = next(user for role, user in captured if role == 'improver')
    failures = json.loads(improvement_input)['dev_failures']
    assert {f['id'] for f in failures} <= {r['id'] for r in sample if r['split'] == 'dev'}
    assert not {f['id'] for f in failures} & {r['id'] for r in sample if r['split'] == 'test'}


def test_stop_before_worker_starts_retains_stopped_state(tmp_path):
    import asyncio
    from factcheck.config import Config
    from eval.fever_fast import Experiment
    dataset = tmp_path / 'sample.json'
    dataset.write_text(json.dumps({'metadata': {}, 'items': select_sample(rows(), per_label=1)}))
    exp = Experiment(dataset, tmp_path / 'results')
    async def run():
        exp.start(Config(mock=False, crusoe_api_key='test-key'))
        await exp.stop()
    asyncio.run(run())
    assert exp.snapshot()['status'] == 'stopped'


def test_missing_verdict_cannot_score_as_not_enough_info(tmp_path, monkeypatch):
    import asyncio
    from factcheck.config import Config
    from factcheck.llm import LLM
    from eval.fever_fast import Experiment
    dataset = tmp_path / 'sample.json'
    dataset.write_text(json.dumps({'metadata': {}, 'items': select_sample(rows(), per_label=1)}))
    async def missing(self, *args, **kwargs):
        return {}
    monkeypatch.setattr(LLM, 'json', missing)
    exp = Experiment(dataset, tmp_path / 'results')
    async def run():
        exp.start(Config(mock=False, crusoe_api_key='test-key'))
        await exp.task
    asyncio.run(run())
    assert exp.state['results']['baseline_dev']['correct'] == 0
    assert exp.state['results']['baseline_test']['correct'] == 0
    assert exp.state['results']['baseline_test']['errors'] == 3
    assert 'Inconclusive' in exp.state['outcome']


def test_judge_only_run_does_not_require_search_credentials(tmp_path, monkeypatch):
    import asyncio
    from eval.fever_fast import Experiment
    monkeypatch.setenv('CRUSOE_API_KEY', 'test-key')
    monkeypatch.delenv('YDC_API_KEY', raising=False)
    monkeypatch.delenv('FC_MOCK', raising=False)
    dataset = tmp_path / 'sample.json'
    dataset.write_text(json.dumps({'metadata': {}, 'items': select_sample(rows(), per_label=1)}))
    exp = Experiment(dataset, tmp_path / 'results')
    async def run():
        exp.start()
        await exp.stop()
    asyncio.run(run())
    assert exp.snapshot()['status'] == 'stopped'
