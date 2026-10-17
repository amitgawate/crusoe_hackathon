import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from factcheck.config import Config
from factcheck.llm import LLM


@pytest.mark.parametrize(
    'role,strong,model,expected',
    [
        ('extractor', False, 'nvidia/Nemotron-3.5-Lightning-30B-A3B', False),
        ('judge', True, 'nvidia/Nemotron-3.5-Lightning-30B-A3B', None),
        ('extractor', False, 'another-provider/model', None),
    ],
)
def test_extraction_reserves_nemotron_output_for_claims(role, strong, model, expected):
    async def check():
        llm = LLM(Config(mock=False, crusoe_api_key='test-key', model_fast=model))
        create = AsyncMock(return_value=SimpleNamespace(
            usage=None, choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content='{"claims": []}'))]))
        llm.client.chat.completions.create = create
        try:
            assert await llm.complete(role, 'system', 'article', strong) == '{"claims": []}'
            body = create.call_args.kwargs.get('extra_body') or {}
            assert body.get('chat_template_kwargs', {}).get('enable_thinking') is expected
        finally:
            await llm.client.close()

    asyncio.run(check())


@pytest.mark.parametrize('first_content,model,expected_thinking', [
    ('', 'nvidia/Nemotron-3.5-Lightning-30B-A3B', False),
    ('{"argument": "unfinished', 'nvidia/Nemotron-3.5-Lightning-30B-A3B', False),
    ('', 'another-provider/model', None),
])
def test_truncated_replies_retry_without_returning_partial_output(
        monkeypatch, first_content, model, expected_thinking):
    monkeypatch.setattr('factcheck.llm.asyncio.sleep', AsyncMock())

    async def check():
        llm = LLM(Config(mock=False, crusoe_api_key='test-key', model_fast=model))
        replies = [SimpleNamespace(usage=None, choices=[SimpleNamespace(
            finish_reason=reason, message=SimpleNamespace(content=content))])
            for reason, content in [('length', first_content), ('stop', '{"argument": "complete"}')]]
        create = AsyncMock(side_effect=replies)
        llm.client.chat.completions.create = create
        try:
            result = await llm.complete('advocate', 'system', 'evidence')
            assert result == '{"argument": "complete"}'
            assert create.await_count == 2
            body = create.call_args_list[1].kwargs.get('extra_body') or {}
            assert body.get('chat_template_kwargs', {}).get('enable_thinking') is expected_thinking
        finally:
            await llm.client.close()

    asyncio.run(check())
