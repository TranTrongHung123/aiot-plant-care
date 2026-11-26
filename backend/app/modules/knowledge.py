"""Versioned, local retrieval over the reviewed Plant Care knowledge base."""

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.trained_ai import LEAF_LABELS

CORPUS_PATH = Path(__file__).resolve().parents[1] / "data" / "plant_knowledge_v1.json"
PUBLIC_HOSTS = {
    "hdu-tomato": "media.hdu.edu.vn",
    "isu-container": "yardandgarden.extension.iastate.edu",
    "knvn-late-blight": "khuyennongvn.gov.vn",
    "umn-spots": "extension.umn.edu",
    "umn-leaf-mold": "extension.umn.edu",
    "uf-target": "ask.ifas.ufl.edu",
    "uc-leaf-curl": "ipm.ucanr.edu",
    "umn-viruses": "extension.umn.edu",
    "uc-spider-mites": "ipm.ucanr.edu",
}
INTERNAL_PATHS = {
    "project-contracts": "docs/contracts.md",
    "project-alerts": "docs/monitoring-alerts.md",
}
ID = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)]
Text = Annotated[str, Field(min_length=1, max_length=720)]
COMPARISON = re.compile(r"\b(?:so sanh|so voi|khac nhau|khac .{1,80} the nao|phan biet)\b")
IMAGE_REFERENCE = re.compile(r"\b(?:anh|la) (?:do|nay|luc nay|vua roi)\b")
MANAGEMENT = re.compile(
    r"\b(?:dieu tri|(?:cach(?: nao)?|nen|de) chua|chua (?:benh|cay|cho cay|moc suong|suong mai|"
    r"sao|the nao|ra sao|duoc khong)|"
    r"cach tri|tri benh|xu ly|phong tru|phong benh|cuu cay|han che lay|ngan benh lan)\b"
)
FOLLOW_UP = re.compile(
    r"\b(?:anh do|anh nay|anh luc nay|anh vua roi|la do|la nay|benh do|benh nay|"
    r"ket qua do|dau hieu do|noi tren|vua noi|vua xem|vua gui|nhu vay)\b|"
    r"^(?:vay|the thi|the con|con)\b"
)
NEW_IMAGE = re.compile(r"\b(?:moi nhat|anh moi|moi chup|hinh moi)\b")


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: ID
    title: Text
    publisher: Text
    scope: Text
    revision: Text
    url: Text | None = None
    path: Text | None = None

    @model_validator(mode="after")
    def check_location(self):
        if self.id in INTERNAL_PATHS and self.path == INTERNAL_PATHS[self.id] and not self.url:
            return self
        if self.id in PUBLIC_HOSTS and self.url and not self.path:
            url = urlsplit(self.url)
            if (
                url.scheme == "https"
                and url.netloc == PUBLIC_HOSTS[self.id]
                and url.path.startswith("/")
                and not url.query
                and not url.fragment
                and not any(ch.isspace() for ch in self.url)
            ):
                return self
        raise ValueError("Địa chỉ nguồn nằm ngoài danh mục đã duyệt.")


class Passage(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: ID
    document: ID
    section: Text
    topic: Literal[
        "leaf", "leaf_management", "leaf_compare", "pot", "harvest", "care", "iot", "alerts"
    ]
    leaf_label: Text | None = None
    aliases: list[Text] = Field(min_length=1, max_length=30)
    answer: Text

    @model_validator(mode="after")
    def check_plain_text(self):
        if (self.topic in {"leaf", "leaf_management"}) != bool(self.leaf_label):
            raise ValueError("Đoạn tra cứu bệnh lá cần mã nhãn mô hình.")
        for value in (self.section, *self.aliases, self.answer):
            if re.search(r"(?i)(?:https?://|javascript:|\bsk-[\w-]{12,}|[<>\x00-\x1f])", value):
                raise ValueError("Đoạn tra cứu cần dùng văn bản thuần.")
        return self


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: ID
    documents: list[Document] = Field(min_length=1, max_length=50)
    passages: list[Passage] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_references(self):
        document_ids = {doc.id for doc in self.documents}
        if len(document_ids) != len(self.documents) or len(
            {passage.id for passage in self.passages}
        ) != len(self.passages):
            raise ValueError("Mã nguồn hoặc mã đoạn bị trùng.")
        if document_ids != {passage.document for passage in self.passages}:
            raise ValueError("Mỗi đoạn cần nguồn hợp lệ, mỗi nguồn cần ít nhất một đoạn.")
        if len({(p.topic, p.leaf_label) for p in self.passages if p.leaf_label}) != sum(
            p.leaf_label is not None for p in self.passages
        ):
            raise ValueError("Mỗi nhãn mô hình cần một đoạn đối chiếu chính.")
        return self


class Citation(BaseModel):
    document_id: str
    title: str
    publisher: str
    section: str
    version: str
    revision: str
    url: str | None = None


class KnowledgeUnavailable(Exception):
    pass


def normalize_query(value: str) -> str:
    value = unicodedata.normalize("NFD", value.casefold().replace("đ", "d"))
    plain = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(re.findall(r"[a-z0-9]+", plain))


def references_previous(question: str) -> bool:
    query = normalize_query(question)
    return bool(
        (FOLLOW_UP.search(query) or MANAGEMENT.search(query)) and not NEW_IMAGE.search(query)
    )


def asks_management(question: str) -> bool:
    return bool(MANAGEMENT.search(normalize_query(question)))


@lru_cache(maxsize=1)
def load_corpus():
    try:
        corpus = Corpus.model_validate_json(CORPUS_PATH.read_bytes())
    except (OSError, ValueError):
        raise KnowledgeUnavailable from None
    passages = [
        (
            passage,
            {normalize_query(alias) for alias in [*passage.aliases, passage.leaf_label or ""]}
            - {""},
        )
        for passage in corpus.passages
    ]
    return corpus.version, {doc.id: doc for doc in corpus.documents}, passages


def lookup(
    question: str,
    *,
    leaf_context: bool = False,
    leaf_label: str | None = None,
    prior_refs: tuple[str, ...] = (),
):
    version, documents, passages = load_corpus()
    query = f" {normalize_query(question)} "
    direct = []
    for passage, aliases in passages:
        if passage.topic == "leaf_management":
            continue
        lengths = [len(alias.split()) for alias in aliases if f" {alias} " in query]
        if lengths:
            direct.append((max(lengths), passage.id, passage))
    comparing = (
        bool(COMPARISON.search(query)) and sum(p.leaf_label is not None for _, _, p in direct) >= 2
    )
    matches = direct
    management = bool(MANAGEMENT.search(query))
    labels = {p.leaf_label for _, _, p in direct if p.leaf_label}
    if management and (labels or leaf_context or (not direct and prior_refs)):
        if not labels and leaf_label:
            labels = {leaf_label}
        if not labels:
            labels = {
                p.leaf_label
                for p, _ in passages
                if p.leaf_label and f"{version}:{p.id}" in prior_refs
            }
            if len(labels) > 1:
                labels.clear()
        matches = [
            (100, p.id, p)
            for p, _ in passages
            if p.topic == "leaf_management" and p.leaf_label in labels
        ]
    elif leaf_context and not comparing:
        matches = [item for item in direct if item[2].topic not in {"leaf", "leaf_compare"}]
        matches.extend(
            (100, p.id, p)
            for p, _ in passages
            if p.topic == "leaf" and leaf_label and p.leaf_label == leaf_label
        )
    if not matches and not management and prior_refs and references_previous(question):
        index = {ref: len(prior_refs) - i for i, ref in enumerate(prior_refs)}
        related = {
            p.leaf_label: index[f"{version}:{p.id}"]
            for p, _ in passages
            if p.topic == "leaf_management" and f"{version}:{p.id}" in index
        }
        matches = [
            (score, p.id, p)
            for p, _ in passages
            if p.topic != "leaf_management"
            and (not leaf_context or p.topic not in {"leaf", "leaf_compare"})
            and (score := index.get(f"{version}:{p.id}") or related.get(p.leaf_label))
        ]

    results = []
    for _, _, passage in sorted(matches, key=lambda match: (-match[0], match[1]))[:3]:
        doc = documents[passage.document]
        citation = Citation(
            document_id=doc.id,
            title=doc.title,
            publisher=doc.publisher,
            section=passage.section,
            version=version,
            revision=doc.revision,
            url=doc.url,
        )
        label = LEAF_LABELS.get(passage.leaf_label) if comparing else None
        answer = f"{label} — {passage.answer}" if label else passage.answer
        results.append((f"{version}:{passage.id}", answer, citation))
    return results
