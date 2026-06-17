import re
from pathlib import Path

import yaml


def load_knowledge_base(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class KnowledgeBase:
    def __init__(self, path: Path, db=None):
        self.data = load_knowledge_base(path)
        self._db = db
        self._learned_faq: list[dict] = []
        self._learned_faq_lookup: dict[str, str] = {}
        self._learned_faq_entries: list[tuple[set[str], dict]] = []
        self._precompute()
        if db is not None:
            self._load_learned_faqs()

    def _load_learned_faqs(self):
        """Load learned FAQs from the database into memory."""
        try:
            for entry in self._db.list_learned_faq(limit=1000):
                self.add_learned_faq(entry["question"], entry["answer"], persist=False)
        except Exception:
            # Database may not be available during some import paths/tests
            pass

    def _precompute(self):
        """Pre-tokenize FAQ/services and build an exact-match FAQ lookup."""
        self._faq_entries = []
        for entry in self.get_faq():
            question = entry.get("question", "")
            answer = entry.get("answer", "")
            candidate = f"{question} {answer}"
            self._faq_entries.append((self._tokenize(candidate), entry))

        self._service_entries = []
        for service in self.get_services():
            candidate = " ".join(
                [
                    service.get("category", ""),
                    service.get("description", ""),
                    *service.get("items", []),
                ]
            )
            self._service_entries.append((self._tokenize(candidate), service))

        self._faq_lookup: dict[str, str] = {}
        for entry in self.get_faq():
            question = entry.get("question", "")
            answer = entry.get("answer", "")
            if question and answer:
                self._faq_lookup[self._normalize(question)] = answer

    def get_company(self) -> dict:
        return self.data.get("company", {})

    def get_services(self) -> list[dict]:
        return self.data.get("services", [])

    def get_process(self) -> dict:
        return self.data.get("process", {})

    def get_faq(self) -> list[dict]:
        return self.data.get("faq", [])

    def get_portfolio(self) -> dict:
        return self.data.get("portfolio", {})

    def get_legal(self) -> dict:
        return self.data.get("legal", {})

    def get_testimonials(self) -> list[dict]:
        return self.data.get("testimonials", [])

    def get_booking(self) -> dict:
        return self.data.get("booking", {})

    def get_group_keywords(self) -> dict[str, list[str]]:
        return self.data.get("group_keywords", {})

    def get_welcome(self, lang: str) -> str:
        return self.data.get("welcome_messages", {}).get(lang, self.data.get("welcome_messages", {}).get("en", ""))

    @staticmethod
    def _normalize(text: str) -> str:
        """Normalize text for exact-match lookups."""
        if not text:
            return ""
        return re.sub(r"[^\w\s]", "", text.lower()).strip()

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """Extract lowercase word tokens from a text."""
        if not text:
            return set()
        return set(re.findall(r"\b\w+\b", text.lower()))

    @classmethod
    def _score_overlap(cls, query_tokens: set[str], candidate_tokens: set[str]) -> int:
        """Return the number of shared word tokens between two token sets."""
        if not query_tokens or not candidate_tokens:
            return 0
        return len(query_tokens & candidate_tokens)

    def add_learned_faq(self, question: str, answer: str, persist: bool = True) -> None:
        """Add a learned FAQ entry to the runtime knowledge base.

        Learned FAQs are checked first in exact-match lookups and included in
        relevance scoring. When ``persist`` is True and a database was provided,
        the entry is also written to the database.
        """
        if not question or not answer:
            return
        normalized = self._normalize(question)
        if not normalized:
            return
        entry = {"question": question, "answer": answer}
        self._learned_faq.append(entry)
        self._learned_faq_lookup[normalized] = answer
        self._learned_faq_entries.append((self._tokenize(f"{question} {answer}"), entry))
        if persist and self._db is not None:
            try:
                self._db.add_learned_faq(question, answer)
            except Exception:
                pass

    def find_exact_faq_answer(self, user_message: str) -> str | None:
        """Return the FAQ answer if the message matches a known question exactly.

        The comparison is case-insensitive and ignores punctuation, so small
        variations like "Was kostet ein Fotoshooting?" and "was kostet ein fotoshooting"
        are treated as matches. Learned FAQs are checked before static YAML FAQs.
        """
        normalized = self._normalize(user_message)
        if not normalized:
            return None
        return self._learned_faq_lookup.get(normalized) or self._faq_lookup.get(normalized)

    def find_relevant_context(
        self, user_message: str, max_faq: int = 3, max_services: int = 2
    ) -> str | None:
        """Build a focused prompt context from FAQ and services relevant to the user message.

        Returns None if no relevant sections are found, so callers can fall back
        to the full knowledge context.
        """
        if not user_message:
            return None

        query_tokens = self._tokenize(user_message)
        if not query_tokens:
            return None

        faq_scores = []
        for tokens, entry in self._learned_faq_entries + self._faq_entries:
            score = self._score_overlap(query_tokens, tokens)
            if score > 0:
                faq_scores.append((score, entry))

        service_scores = []
        for tokens, service in self._service_entries:
            score = self._score_overlap(query_tokens, tokens)
            if score > 0:
                service_scores.append((score, service))

        if not faq_scores and not service_scores:
            return None

        faq_scores.sort(key=lambda x: x[0], reverse=True)
        service_scores.sort(key=lambda x: x[0], reverse=True)

        lines = []
        if service_scores:
            lines.append("# Relevante Leistungen")
            for _, service in service_scores[:max_services]:
                lines.append(f"\n{service.get('icon', '')} {service.get('category', '')}")
                lines.append(service.get("description", ""))
                for item in service.get("items", []):
                    lines.append(f"  - {item}")

        if faq_scores:
            lines.append("\n# Relevante FAQ")
            for _, entry in faq_scores[:max_faq]:
                lines.append(f"Q: {entry.get('question', '')}")
                lines.append(f"A: {entry.get('answer', '')}")

        return "\n".join(lines)

    def to_prompt_context(self) -> str:
        """Convert knowledge base into a concise string for the LLM system prompt."""
        company = self.get_company()
        services = self.get_services()
        process = self.get_process()
        faq = self.get_faq()
        legal = self.get_legal()

        lines = [
            "# Unternehmensinformationen",
            f"Name: {company.get('name', '')}",
            f"Slogan: {company.get('slogan', '')}",
            f"Beschreibung: {company.get('description', '')}",
            f"Adresse: {company.get('location', '')}",
            f"Telefon: {company.get('phone', '')}",
            f"E-Mail: {company.get('email', '')}",
            f"Webseite: {company.get('website', '')}",
            f"Instagram: {company.get('instagram', '')}",
            f"Facebook: {company.get('facebook', '')}",
            "",
            "# Leistungen",
        ]

        for service in services:
            lines.append(f"\n{service.get('icon', '')} {service.get('category', '')}")
            lines.append(service.get("description", ""))
            for item in service.get("items", []):
                lines.append(f"  - {item}")

        lines.append("\n# Ablauf")
        for step in process.get("steps", []):
            lines.append(f"{step.get('number', '')} {step.get('title', '')}: {step.get('description', '')}")

        lines.append("\n# FAQ")
        for entry in faq:
            lines.append(f"Q: {entry.get('question', '')}")
            lines.append(f"A: {entry.get('answer', '')}")

        lines.append(f"\n# Rechtliches\nImpressum: {legal.get('imprint', '')}\nDatenschutz: {legal.get('privacy', '')}")

        if self._learned_faq:
            lines.append("\n# Gelernte Antworten")
            for entry in self._learned_faq:
                lines.append(f"Q: {entry['question']}")
                lines.append(f"A: {entry['answer']}")

        return "\n".join(lines)
