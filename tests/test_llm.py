import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TELEGRAM_BOT_TOKEN"] = "123456:dummy-token-for-testing-only"
os.environ["ANTHROPIC_API_KEY"] = "dummy-anthropic-key"
os.environ["LLM_PROVIDER"] = "anthropic"

from src import config
from src.knowledge import KnowledgeBase
from src.llm import AnthropicClient, BaseLLMClient, OpenAICompatibleClient, ResponseGuard, TTLCache, _PRICE_PATTERN

# Ensure predictable config for tests
config.LLM_TIMEOUT = 5
config.LLM_MAX_RETRIES = 2


class MockKnowledgeBase(KnowledgeBase):
    def __init__(self, data: dict):
        self.data = data
        self._learned_faq: list[dict] = []
        self._learned_faq_lookup: dict[str, str] = {}
        self._learned_faq_entries: list[tuple[set[str], dict]] = []
        self._precompute()

    def to_prompt_context(self) -> str:
        return self.data.get("context", "")


def test_extract_prices():
    text = "Fotoshooting ab € 199. Aktion: €50. Webseite $1,200 oder EUR 99."
    prices = BaseLLMClient._extract_prices(text)
    assert "€199" in prices
    assert "€50" in prices
    assert "$1,200" in prices
    assert "eur99" in prices
    print("✓ Price extraction tests passed")


def test_validate_response_allows_known_prices():
    kb = MockKnowledgeBase({"context": "Leistung ab € 199. Paket € 49."})
    client = AnthropicClient(kb)
    assert client.validate_response("Das Paket kostet € 49.") is True
    assert client.validate_response("Die Leistung ab € 199 ist buchbar.") is True
    print("✓ Validate response allows known prices tests passed")


def test_validate_response_rejects_unknown_prices():
    kb = MockKnowledgeBase({"context": "Leistung ab € 199."})
    client = AnthropicClient(kb)
    assert client.validate_response("Das kostet € 999.") is False
    assert client.validate_response("Nur $50 heute.") is False
    print("✓ Validate response rejects unknown prices tests passed")


def test_safe_reply_localization():
    client = AnthropicClient(MockKnowledgeBase({"context": ""}))
    assert "zufriedenstellende Antwort" in client.safe_reply("de")
    assert "/human" in client.safe_reply("en")
    assert client.safe_reply("xx") == client.safe_reply("en")
    print("✓ Safe reply localization tests passed")


def test_focused_context_used_when_relevant():
    kb = MockKnowledgeBase(
        {
            "context": "FULL CONTEXT",
            "faq": [
                {
                    "question": "Was kostet ein Fotoshooting?",
                    "answer": "Ab € 199.",
                },
                {
                    "question": "Wie buche ich?",
                    "answer": "Über das Kontaktformular.",
                },
            ],
            "services": [
                {
                    "category": "Fotografie",
                    "description": "Professionelle Fotoshootings",
                    "items": ["Hochzeit", "Portrait"],
                    "icon": "📸",
                },
                {
                    "category": "Webdesign",
                    "description": "Moderne Webseiten",
                    "items": ["Landingpage", "Shop"],
                    "icon": "🌐",
                },
            ],
        }
    )
    client = AnthropicClient(kb)
    prompt = client.build_system_prompt("de", user_message="Fotoshooting Hochzeit Preis")
    assert "Fotografie" in prompt
    assert "Was kostet ein Fotoshooting?" in prompt
    assert "FULL CONTEXT" not in prompt
    print("✓ Focused context used when relevant tests passed")


def test_full_context_fallback_when_no_overlap():
    kb = MockKnowledgeBase(
        {
            "context": "FULL CONTEXT",
            "faq": [{"question": "Wie buche ich?", "answer": "Über das Formular."}],
            "services": [
                {
                    "category": "Webdesign",
                    "description": "Webseiten",
                    "items": ["Landingpage"],
                    "icon": "🌐",
                }
            ],
        }
    )
    client = AnthropicClient(kb)
    prompt = client.build_system_prompt("de", user_message="xyzabc irrelevant")
    assert "FULL CONTEXT" in prompt
    print("✓ Full context fallback tests passed")


class CountingLLMClient(BaseLLMClient):
    def __init__(self, knowledge, responses):
        super().__init__(knowledge)
        self._responses = responses
        self._calls = 0

    async def _call_api(
        self, system_prompt: str, messages: list[dict]
    ) -> tuple[str, dict | None]:
        response = self._responses[self._calls]
        self._calls += 1
        return response, None


def test_retry_succeeds_after_transient_failure():
    kb = MockKnowledgeBase({"context": ""})
    client = CountingLLMClient(kb, ["Antwort eins für Sie", "Antwort zwei für Sie"])
    attempts = {"count": 0}

    class TransientError(Exception):
        pass

    original_call = client._call_api

    async def failing_then_success(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TransientError("boom")
        return await original_call(*args, **kwargs)

    client._call_api = failing_then_success

    result = run_async(client.chat([], lang="de"))
    assert result == "Antwort eins für Sie"
    assert attempts["count"] == 2
    print("✓ Retry after transient failure tests passed")


def test_retry_exhausted_raises():
    kb = MockKnowledgeBase({"context": ""})
    client = CountingLLMClient(kb, [])

    async def always_fail(*args, **kwargs):
        raise RuntimeError("persistent failure")

    client._call_api = always_fail

    try:
        run_async(client.chat([], lang="de"))
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "persistent failure" in str(e)
    print("✓ Retry exhausted raises tests passed")


def test_validation_failure_returns_safe_reply():
    kb = MockKnowledgeBase({"context": ""})
    client = CountingLLMClient(kb, ["Das kostet € 999."])
    result = run_async(client.chat([], lang="de"))
    assert "/human" in result
    print("✓ Validation failure fallback tests passed")


def test_guard_blocks_informal_german():
    guard = ResponseGuard()
    assert guard.check("Kann ich Ihnen helfen?", lang="de") is True
    assert guard.check("Kann ich dir helfen?", lang="de") is False
    assert guard.check("Wie kann ich euch helfen?", lang="de") is False
    print("✓ Guard blocks informal German tests passed")


def test_guard_blocks_instruction_leaks():
    guard = ResponseGuard()
    assert guard.check("Als KI-Modell habe ich keine Meinung.", lang="de") is False
    assert guard.check("Meine Anweisungen sagen das nicht.", lang="de") is False
    assert guard.check("Gerne helfe ich Ihnen weiter.", lang="de") is True
    print("✓ Guard blocks instruction leaks tests passed")


def test_timeout_is_applied():
    kb = MockKnowledgeBase({"context": ""})
    client = CountingLLMClient(kb, ["ok"])

    async def slow_call(*args, **kwargs):
        import asyncio

        await asyncio.sleep(10)
        return "too late", None

    client._call_api = slow_call
    original_timeout = config.LLM_TIMEOUT
    original_retries = config.LLM_MAX_RETRIES
    config.LLM_TIMEOUT = 1
    config.LLM_MAX_RETRIES = 0
    try:
        start = time.time()
        try:
            run_async(client.chat([], lang="de"))
            assert False, "Expected timeout"
        except Exception:
            pass
        elapsed = time.time() - start
        assert elapsed < 3, f"Timeout took too long: {elapsed}"
    finally:
        config.LLM_TIMEOUT = original_timeout
        config.LLM_MAX_RETRIES = original_retries
    print("✓ Timeout applied tests passed")


def test_ttl_cache_basic():
    cache = TTLCache(ttl_seconds=0.5, max_entries=10)
    cache.set("a", "value-a")
    assert cache.get("a") == "value-a"
    assert len(cache) == 1
    time.sleep(0.6)
    assert cache.get("a") is None
    print("✓ TTL cache basic tests passed")


def test_ttl_cache_respects_max_entries():
    cache = TTLCache(ttl_seconds=60, max_entries=4)
    for i in range(6):
        cache.set(str(i), f"value-{i}")
    assert len(cache) <= 4
    print("✓ TTL cache max entries tests passed")


def test_llm_response_cache_hit():
    kb = MockKnowledgeBase({"context": ""})
    client = CountingLLMClient(kb, ["Gern helfe ich Ihnen weiter."])

    async def call_twice():
        first = await client.chat([{"role": "user", "content": "Hallo"}], lang="de")
        second = await client.chat([{"role": "user", "content": "Hallo"}], lang="de")
        return first, second

    first, second = run_async(call_twice())
    assert first == second
    assert client._calls == 1, "Expected only one underlying API call due to cache"
    print("✓ LLM response cache hit tests passed")


def run_async(coro):
    import asyncio

    return asyncio.run(coro)


if __name__ == "__main__":
    test_extract_prices()
    test_validate_response_allows_known_prices()
    test_validate_response_rejects_unknown_prices()
    test_safe_reply_localization()
    test_focused_context_used_when_relevant()
    test_full_context_fallback_when_no_overlap()
    test_retry_succeeds_after_transient_failure()
    test_retry_exhausted_raises()
    test_validation_failure_returns_safe_reply()
    test_guard_blocks_informal_german()
    test_guard_blocks_instruction_leaks()
    test_timeout_is_applied()
    test_ttl_cache_basic()
    test_ttl_cache_respects_max_entries()
    test_llm_response_cache_hit()
    print("\nLLM tests passed!")
