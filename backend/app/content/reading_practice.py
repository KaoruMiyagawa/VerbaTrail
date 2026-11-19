"""Versioned Academic articles and their projection into the shared content importer."""

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from string import ascii_uppercase
from typing import Any

PACK_ID = "verbatrail-academic-reading-v1"
DOMAIN = "ielts_academic"
TOPIC_PREFIX = "ielts-reading-practice-"
RULE_VERSION = "reading-passage-exact-v1"
SOURCE = "Original VerbaTrail Academic Reading practice"
ARTICLES = ("urban-green-spaces-v1", "the-science-of-memory-v1", "ocean-exploration-v1")
SECTION_INSTRUCTIONS = {
    "tfng": "Choose True if the statement agrees with the passage, False if it contradicts the passage, or Not Given if the passage does not say.",
    "multiple_choice": "Choose the one answer that best fits the passage.",
    "matching_headings": "Choose the best heading for each listed paragraph. Use each heading at most once. Two headings are unused.",
}
TFNG = dict(zip(("true", "false", "not_given"), ("True", "False", "Not Given"), strict=True))
ROMAN = ("i", "ii", "iii", "iv", "v", "vi", "vii")
WORD = re.compile(r"[A-Za-z]+(?:['-][A-Za-z]+)*")


@dataclass(frozen=True)
class Item:
    id: str
    number: int
    prompt: str
    options: dict[str, str]
    answer: str
    explanation: str
    paragraph: str
    quote: str

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "number": self.number, "prompt": self.prompt,
                "options": [{"id": key, "text": text} for key, text in self.options.items()]}


@dataclass(frozen=True)
class Section:
    type: str
    items: tuple[Item, ...]

    def public(self) -> dict[str, Any]:
        return {"type": self.type, "instructions": SECTION_INSTRUCTIONS[self.type],
                "items": [item.public() for item in self.items]}


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    theme: str
    summary: str
    difficulty: int
    paragraphs: dict[str, str]
    sections: tuple[Section, ...]

    @property
    def topic_id(self) -> str:
        return TOPIC_PREFIX + self.id

    @property
    def material_id(self) -> str:
        return f"reading-passage-{self.id}"

    @property
    def items(self) -> tuple[Item, ...]:
        return tuple(item for section in self.sections for item in section.items)

    def paragraphs_view(self) -> list[dict[str, str]]:
        return [{"id": label, "text": text} for label, text in self.paragraphs.items()]

    def summary_view(self) -> dict[str, Any]:
        return {
            "id": self.id, "title": self.title, "theme": self.theme, "summary": self.summary,
            "difficulty": self.difficulty, "question_count": len(self.items),
            "word_count": len(WORD.findall(" ".join(self.paragraphs.values()))),
        }

    def public(self) -> dict[str, Any]:
        return self.summary_view() | {"paragraphs": self.paragraphs_view(), "source": SOURCE,
                                      "sections": [section.public() for section in self.sections]}

    def question_rows(self):
        """Yield importer rows together with the items evaluated as each row."""
        for section in self.sections:
            if section.type == "matching_headings":
                group_id = f"{self.id}-headings"
                yield {
                    "id": group_id, "material_id": self.material_id, "question_role": "initial",
                    "question_type": "matching_headings", "prompt_text": SECTION_INSTRUCTIONS[section.type],
                    "standard_answer": "", "answer_explanation": "", "difficulty": self.difficulty,
                    "reading_group": {
                        "exam_variant": "academic",
                        "provenance": {"source": SOURCE, "license": "Repository terms"},
                        "display": {
                            "id": group_id, "section_id": "academic-reading", "display_schema_version": "1",
                            "passage": {"id": self.material_id, "content_version": self.id,
                                        "title": self.title, "paragraphs": self.paragraphs_view()},
                            "options": section.items[0].public()["options"],
                            "items": [{"id": item.id, "paragraph_id": item.paragraph} for item in section.items],
                        },
                        "private_rules": {
                            "rules_version": RULE_VERSION,
                            "answers": {item.id: item.answer for item in section.items},
                            "explanations": {item.id: item.explanation for item in section.items},
                        },
                    },
                }, section.items
            else:
                for item in section.items:
                    yield {
                        "id": item.id, "material_id": self.material_id, "question_role": "initial",
                        "question_type": "multiple_choice", "prompt_text": item.prompt,
                        "standard_answer": item.answer, "answer_explanation": item.explanation,
                        "difficulty": self.difficulty,
                        "evaluation_rubric": {
                            "accepted_answers": [item.answer], "options": item.public()["options"],
                            "content_version": self.id, "rule_version": RULE_VERSION, "format": section.type,
                            "source_reference": {"paragraph": item.paragraph, "quote": item.quote},
                        },
                    }, (item,)

    def seed_topic(self) -> dict[str, Any]:
        return {
            "id": self.topic_id, "slug": self.topic_id, "title": self.title, "summary": self.summary,
            "difficulty_level": self.difficulty,
            "materials": [{
                "id": self.material_id, "title": self.title, "summary": self.summary,
                "content_body": "\n\n".join(self.paragraphs.values()), "content_format": "plain", "version": 1,
            }],
            "questions": [row for row, _ in self.question_rows()],
        }


@lru_cache(maxsize=1)
def reading_articles() -> dict[str, Article]:
    articles = {}
    for article_id in ARTICLES:
        path = Path(__file__).parent / "reading" / f"{article_id}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        paragraphs = dict(zip(ascii_uppercase, data["paragraphs"]))
        sections = []
        number = 0
        for raw in data["sections"]:
            kind = raw["type"]
            items = []
            for authored in raw["questions"]:
                number += 1
                if kind == "tfng":
                    options = TFNG
                elif kind == "matching_headings":
                    options = dict(zip(ROMAN, raw["options"], strict=True))
                elif kind == "multiple_choice":
                    options = dict(zip("ABCD", authored["options"], strict=True))
                else:
                    raise ValueError(f"{article_id}: unknown question format {kind}")
                item = Item(id=f"{article_id}-q{number}", number=number, options=options, **authored_for_item(authored))
                if (item.answer not in options or len(set(options.values())) != len(options)
                        or not item.quote.strip() or item.quote not in paragraphs.get(item.paragraph, "")
                        or not item.explanation.strip() or not item.prompt.strip()):
                    raise ValueError(f"{item.id}: invalid option, explanation or source quotation")
                items.append(item)
            if kind == "matching_headings" and (
                len(raw["options"]) - len(items) != 2
                or len({item.answer for item in items}) != len(items)
                or len({item.paragraph for item in items}) != len(items)
            ):
                raise ValueError(f"{article_id}: invalid heading group")
            sections.append(Section(kind, tuple(items)))
        article = Article(article_id, data["title"], data["theme"], data["summary"], data["difficulty"],
                          paragraphs, tuple(sections))
        if data["id"] != article_id or not 600 <= article.summary_view()["word_count"] <= 850 or not 10 <= number <= 14:
            raise ValueError(f"{article_id}: invalid edition, word count or question count")
        articles[article_id] = article
    return articles


def authored_for_item(authored: dict[str, Any]) -> dict[str, str]:
    return {key: authored[key] for key in ("prompt", "answer", "explanation", "paragraph", "quote")}


def reading_practice_pack() -> dict[str, Any]:
    return {"pack_id": PACK_ID, "domain_code": DOMAIN,
            "topics": [article.seed_topic() for article in reading_articles().values()]}
