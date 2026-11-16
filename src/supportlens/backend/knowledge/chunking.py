"""Deterministic text segmentation and revision-specific chunk identity."""

from dataclasses import dataclass
import hashlib
import re
import unicodedata
from uuid import UUID, uuid5

MAX_CHARS = 900
OVERLAP = 120
CHUNK_FORMAT = "heading-char-v1"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    return re.sub(r"\n{3,}", "\n\n", "\n".join(line.strip() for line in text.split("\n"))).strip()


@dataclass(frozen=True)
class Chunk:
    id: UUID
    ordinal: int
    text: str
    search_text: str
    content_hash: str
    embedding_hash: str


def chunks(revision: UUID, title: str, body: str) -> list[Chunk]:
    sections = re.split(r"(?m)(?=^#{1,6}\s+\S)", normalize(body))
    result = []
    seen = set()
    for section in sections:
        section = section.strip()
        if not section:
            continue
        first_line, _, rest = section.partition("\n")
        heading = first_line[:100] if re.match(r"^#{1,6}\s", first_line) and rest else ""
        content = rest.strip() if heading else section
        capacity = MAX_CHARS - len(heading) - 1
        start = 0
        while start < len(content):
            end = min(start + capacity, len(content))
            if end < len(content):
                boundary = max(content.rfind(" ", start + capacity // 2, end), content.rfind("\n", start + capacity // 2, end))
                if boundary != -1:
                    end = boundary
            excerpt = "\n".join(part for part in (heading, content[start:end].strip()) if part)
            content_hash = digest(excerpt)
            if excerpt and content_hash not in seen:
                seen.add(content_hash)
                searchable = f"{normalize(title)}\n{excerpt}"
                result.append(Chunk(uuid5(revision, f"{CHUNK_FORMAT}:{content_hash}"), len(result), excerpt,
                                    searchable, digest(excerpt), digest(searchable)))
            if end == len(content):
                break
            start = max(start + 1, end - OVERLAP)
    return result
