"""Tests for kitegen.llm — adapters, usage, tracker recording."""

import kitegen as kg


class _FakeUsage:
    def __init__(self, tokens):
        self.prompt_tokens = tokens[0]
        self.completion_tokens = tokens[1]
        self.total_tokens = tokens[0] + tokens[1]


class _FakeChoice:
    def __init__(self):
        self.message = type("M", (), {"content": "hello", "tool_calls": None})()


class _FakeResponse:
    def __init__(self, tokens):
        self.choices = [_FakeChoice()]
        self.usage = _FakeUsage(tokens)


def _fake_client(tokens):
    class FakeChat:
        async def create(self, **kwargs):
            return _FakeResponse(tokens)

    client = type("C", (), {})()
    client.chat = type("CC", (), {"completions": FakeChat()})()
    return client


def test_openai_adapter_timeout_defaults_to_120_and_is_configurable(monkeypatch):
    """Timeout defaults to 120 s, accepts constructor arg and LLM_TIMEOUT env var."""
    assert kg.OpenAIAdapter(model="gpt-4o", client=_fake_client((1, 1))).timeout == 120.0
    assert kg.OpenAIAdapter(model="gpt-4o", client=_fake_client((1, 1)), timeout=60.0).timeout == 60.0

    monkeypatch.setenv("LLM_TIMEOUT", "45")
    assert kg.OpenAIAdapter(model="gpt-4o", client=_fake_client((1, 1))).timeout == 45.0

    # Constructor arg wins over env var
    assert kg.OpenAIAdapter(model="gpt-4o", client=_fake_client((1, 1)), timeout=90.0).timeout == 90.0


async def test_openai_adapter_records_usage_to_tracker():
    """chat() records usage into the adapter's TokenTracker."""
    tracker = kg.TokenTracker()
    adapter = kg.OpenAIAdapter(
        model="deepseek-v4-flash", client=_fake_client((100, 50)), tracker=tracker,
    )

    resp = await adapter.chat([kg.Message(role="user", content="hi")])

    assert resp.usage.prompt_tokens == 100
    assert resp.usage.completion_tokens == 50
    assert tracker.total_tokens() == 150
    assert tracker.total_cost() > 0  # v4-flash is in the pricing table


async def test_openai_adapter_without_tracker_does_not_crash():
    """No tracker — chat() just returns the response."""
    adapter = kg.OpenAIAdapter(model="gpt-4o", client=_fake_client((10, 5)))
    resp = await adapter.chat([kg.Message(role="user", content="hi")])
    assert resp.content == "hello"
    assert resp.usage.total_tokens == 15


async def test_pricing_covers_deepseek_v4():
    """The new V4 models have pricing entries."""
    u = kg.Usage(prompt_tokens=1000, completion_tokens=1000, total_tokens=2000)
    assert u.cost("deepseek-v4-flash") > 0
    assert u.cost("deepseek-v4-pro") > 0
    assert u.cost("unknown-model") == 0.0  # unknown models cost nothing to compute


async def test_tracker_serialization_roundtrip():
    """to_dict()/load_records() survive a simulated process restart."""
    t1 = kg.TokenTracker()
    t1.record("deepseek-v4-flash", kg.Usage(100, 50, 150))

    # "restart": export, create a fresh tracker, load back
    data = t1.to_dict()
    t2 = kg.TokenTracker()
    t2.load_records(data)

    assert t2.total_tokens() == 150
    assert t2.total_cost() == t1.total_cost()
    assert t2.by_model() == t1.by_model()
