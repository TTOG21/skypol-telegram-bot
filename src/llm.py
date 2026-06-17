import asyncio
import logging
import re
import time
from abc import ABC, abstractmethod

from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

from src import config
from src.knowledge import KnowledgeBase

logger = logging.getLogger(__name__)

# Matches common price patterns: €100, 100 €, EUR 100, $1,200, etc.
_PRICE_PATTERN = re.compile(
    r"(?:\€|\$|EUR|USD|GBP)\s*\d[\d.,]*|\d[\d.,]*\s*(?:\€|\$|EUR|USD|GBP)",
    re.IGNORECASE,
)


class TTLCache:
    """Simple in-memory TTL cache with a max entry limit.

    Not thread-safe by design; intended for use within a single async event loop.
    """

    def __init__(self, ttl_seconds: float, max_entries: int):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._store: dict[str, tuple[str, float]] = {}

    def _evict_expired(self):
        now = time.monotonic()
        expired = [key for key, (_, expires_at) in self._store.items() if expires_at <= now]
        for key in expired:
            del self._store[key]

    def _make_room(self):
        if len(self._store) < self.max_entries:
            return
        # Evict oldest entries by insertion time (approximated by sorted keys)
        sorted_items = sorted(self._store.items(), key=lambda item: item[1][1])
        to_remove = int(self.max_entries * 0.25) or 1
        for key, _ in sorted_items[:to_remove]:
            del self._store[key]

    def get(self, key: str) -> str | None:
        self._evict_expired()
        value, expires_at = self._store.get(key, (None, 0))
        if value is None or time.monotonic() >= expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str):
        self._evict_expired()
        self._make_room()
        self._store[key] = (value, time.monotonic() + self.ttl_seconds)

    def clear(self):
        self._store.clear()

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)


class ResponseGuard:
    """Lightweight output guardrail for LLM replies."""

    def __init__(self):
        self.formal_markers = [re.compile(r"\bSie\b"), re.compile(r"\bIhnen\b"), re.compile(r"\bIhr\b")]
        self.informal_markers = [re.compile(r"\bdu\b"), re.compile(r"\bihr\b")]
        self.leak_phrases = [
            "als KI",
            "KI-Modell",
            "meine Anweisungen",
            "system prompt",
            "meine Programmierung",
            "meine Instruktionen",
            "meine Trainingsdaten",
        ]

    def check(self, text: str, lang: str = "de") -> bool:
        """Return True if the response passes all guardrail checks."""
        if not text:
            logger.warning("LLM response is empty")
            return False

        lowered = text.lower()

        if lang == "de":
            has_formal = any(pattern.search(text) for pattern in self.formal_markers)
            has_informal = any(pattern.search(lowered) for pattern in self.informal_markers)
            if has_informal:
                logger.warning("LLM response uses informal German address")
                return False
            if not has_formal:
                logger.warning("LLM response missing formal German marker")
                return False

        for phrase in self.leak_phrases:
            if phrase.lower() in lowered:
                logger.warning("LLM response contains instruction leak: %s", phrase)
                return False

        return True


class BaseLLMClient(ABC):
    def __init__(self, knowledge: KnowledgeBase):
        self.model = config.LLM_MODEL
        self.knowledge = knowledge
        self.knowledge_context = knowledge.to_prompt_context()
        self._allowed_prices = self._extract_prices(self.knowledge_context)
        self.guard = ResponseGuard()
        self._response_cache = TTLCache(
            ttl_seconds=config.LLM_CACHE_TTL_SECONDS,
            max_entries=config.LLM_CACHE_MAX_ENTRIES,
        )

    @staticmethod
    def _extract_prices(text: str) -> set[str]:
        """Extract all price-like strings from a text, normalized to lowercase."""
        prices = set()
        for match in _PRICE_PATTERN.finditer(text or ""):
            price = match.group(0).lower().replace(" ", "")
            price = price.rstrip(".,;:!?)")
            prices.add(price)
        return prices

    @staticmethod
    def _cache_key(user_message: str, lang: str, is_group: bool) -> str:
        """Build a stable cache key from the user message and context."""
        normalized = re.sub(r"\s+", " ", user_message.lower().strip())
        return f"{lang}:{int(is_group)}:{normalized}"

    def _get_focused_context(self, user_message: str) -> str:
        """Return focused KB context for the user's message, falling back to the full context."""
        focused = self.knowledge.find_relevant_context(user_message)
        if focused:
            return focused
        return self.knowledge_context

    def build_system_prompt(
        self, lang: str, is_group: bool = False, user_message: str = ""
    ) -> str:
        # Bot always answers in German with a formal tone, regardless of the user's language
        lang_instruction = (
            "Antworte immer auf Deutsch, egal in welcher Sprache der Nutzer schreibt. "
            "Verwende dabei stets den formalen 'Sie'-Ton. Vermeide 'du' oder 'ihr'. "
            "Sei höflich, professionell und klar."
        )

        group_instruction = (
            "Dies ist eine Gruppenchat-Nachricht. Halte die Antwort kurz und höflich. "
            "Erwähne den Nutzer am Anfang mit seinem Namen."
            if is_group
            else "Dies ist ein privater Chat. Antworte ausführlich und hilfreich."
        )

        context = (
            self._get_focused_context(user_message)
            if user_message
            else self.knowledge_context
        )

        return (
            "Du bist der freundliche und professionelle Kundensupport-Assistent von Skypol Arts & Media. "
            "Du beantwortest Fragen ausschließlich auf Basis der folgenden Unternehmensinformationen. "
            "Wenn du eine Frage nicht beantworten kannst, entschuldige dich höflich und biete an, "
            "die Anfrage an das Team weiterzuleiten. "
            "Gib keine Preise an, wenn sie nicht in den Informationen stehen. "
            "Fasse dich kurz und bleibe hilfreich.\n\n"
            f"{lang_instruction}\n"
            f"{group_instruction}\n\n"
            f"{context}"
        )

    def validate_response(self, text: str) -> bool:
        """Return True if the response does not contain hallucinated prices."""
        response_prices = self._extract_prices(text)
        for price in response_prices:
            if price not in self._allowed_prices:
                logger.warning("LLM response contains forbidden price: %s", price)
                return False
        return True

    @staticmethod
    def safe_reply(lang: str) -> str:
        """Localized fallback when the LLM response is rejected."""
        messages = {
            "de": (
                "Entschuldigung, ich kann Ihnen dazu leider keine zufriedenstellende Antwort geben. "
                "Bitte nutzen Sie /human, damit unser Team Ihnen persönlich weiterhilft."
            ),
            "el": (
                "Συγγνώμη, δεν μπορώ να σας δώσω ικανοποιητική απάντηση γι' αυτό. "
                "Χρησιμοποιήστε το /human για να σας βοηθήσει η ομάδα μας προσωπικά."
            ),
            "en": (
                "Sorry, I cannot give you a satisfactory answer to that. "
                "Please use /human so our team can help you personally."
            ),
        }
        return messages.get(lang, messages["en"])

    @staticmethod
    def _last_user_message(messages: list[dict]) -> str:
        """Extract the content of the most recent user message."""
        for message in reversed(messages):
            if message.get("role") == "user":
                return message.get("content", "")
        return ""

    @abstractmethod
    async def _call_api(
        self, system_prompt: str, messages: list[dict]
    ) -> tuple[str, dict | None]:
        """Call the underlying LLM API and return (raw_text, usage_dict).

        usage_dict should contain 'input_tokens' and 'output_tokens' if available.
        """
        pass

    async def chat(self, messages: list[dict], lang: str, is_group: bool = False) -> str:
        user_message = self._last_user_message(messages)
        cache_key = self._cache_key(user_message, lang, is_group)
        cached = self._response_cache.get(cache_key)
        if cached is not None:
            logger.info("LLM response served from cache")
            return cached

        system_prompt = self.build_system_prompt(lang, is_group, user_message)
        last_exception: Exception | None = None

        for attempt in range(config.LLM_MAX_RETRIES + 1):
            try:
                raw_reply, usage = await asyncio.wait_for(
                    self._call_api(system_prompt, messages),
                    timeout=config.LLM_TIMEOUT,
                )
                if usage:
                    logger.info(
                        "LLM usage: input=%s output=%s",
                        usage.get("input_tokens"),
                        usage.get("output_tokens"),
                    )
                reply = raw_reply.strip()
                if self.validate_response(reply) and self.guard.check(reply, lang):
                    self._response_cache.set(cache_key, reply)
                    return reply
                logger.warning("LLM response failed validation on attempt %s", attempt + 1)
                return self.safe_reply(lang)
            except Exception as e:
                last_exception = e
                logger.warning("LLM call failed (attempt %s): %s", attempt + 1, e)
                if attempt < config.LLM_MAX_RETRIES:
                    await asyncio.sleep(1.5 ** attempt)

        if last_exception is not None:
            raise last_exception
        raise RuntimeError("LLM call failed without a specific exception")


class AnthropicClient(BaseLLMClient):
    def __init__(self, knowledge: KnowledgeBase):
        super().__init__(knowledge)
        client_kwargs = {"api_key": config.LLM_API_KEY}
        if config.LLM_BASE_URL:
            client_kwargs["base_url"] = config.LLM_BASE_URL
        self.client = AsyncAnthropic(**client_kwargs)

    async def _call_api(
        self, system_prompt: str, messages: list[dict]
    ) -> tuple[str, dict | None]:
        response = await self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            temperature=0.3,
            system=system_prompt,
            messages=messages,
        )
        text = response.content[0].text if response.content else ""
        usage = None
        if response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "input_tokens", None),
                "output_tokens": getattr(response.usage, "output_tokens", None),
            }
        return text, usage


class OpenAICompatibleClient(BaseLLMClient):
    def __init__(self, knowledge: KnowledgeBase):
        super().__init__(knowledge)
        client_kwargs = {"api_key": config.LLM_API_KEY}
        if config.LLM_BASE_URL:
            client_kwargs["base_url"] = config.LLM_BASE_URL
        self.client = AsyncOpenAI(**client_kwargs)

    async def _call_api(
        self, system_prompt: str, messages: list[dict]
    ) -> tuple[str, dict | None]:
        openai_messages = [{"role": "system", "content": system_prompt}]
        openai_messages.extend(messages)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            max_tokens=1024,
            temperature=0.3,
        )
        text = response.choices[0].message.content or ""
        usage = None
        if response.usage:
            usage = {
                "input_tokens": getattr(response.usage, "prompt_tokens", None),
                "output_tokens": getattr(response.usage, "completion_tokens", None),
            }
        return text, usage


def create_llm_client(knowledge: KnowledgeBase) -> BaseLLMClient:
    if config.LLM_PROVIDER == "anthropic":
        return AnthropicClient(knowledge)
    return OpenAICompatibleClient(knowledge)
