"""Conservative, closed issue representations and deterministic exact cosine search."""

from dataclasses import dataclass
import math
import re

from ..domain.enums import Intent
from ..drafts.grounding import OPENING, UNSAFE, context_policy, normalize, scrub, sentences
from ..knowledge.embedding import DIMENSION

VERSION = "response-cache:en:1"
MIN_SIMILARITY = 0.90
MAX_CANDIDATES = 80
COMMON = frozenset("a an and are as can could during every for help how i in is it keeps me my of on please the this to when while with would you again same fictional synthetic demo sample".split())
NEGATION = re.compile(r"\b(?:no|not|never|without|unless|except|only|instead|cannot|can't|won't|doesn't|don't|isn't)\b", re.I)
PRIVATE = re.compile(r"\b(?:account|address|agent|customer|user|name|profile|plan|(?<!sort )orders?|purchase|invoice|"
                     r"device id|trace id|support case|"
                     r"password|token|secret|identifier|contact|phone|email|billing|payment|refund|security|"
                     r"username|credential|delete|erase|disable|permanently|factory reset|"
                     r"my|mine|our|ours|their|theirs|his|her|hers|your|yours|he|she|we|i)\b", re.I)


@dataclass(frozen=True)
class Issue:
    key: str
    phrase: str


@dataclass(frozen=True)
class Rule:
    intent: Intent
    key: str
    anchors: tuple[frozenset[str], ...]
    variants: tuple[tuple[frozenset[str], str], ...]
    optional: frozenset[str] = frozenset()


def words(value):
    return frozenset(value.split())


RULES = (
    Rule(Intent.APP_TECHNICAL, "playback-interruption", (words("audio music playback player songs song tracks track"),), (
        (words("pause pauses pausing stopped stalls stutters stutter stuttering"),
         "audio playback interruption during a track music pauses while playing"),
        (words("stop stops stopping cutting cuts"),
         "audio playback interruption during a track music stops while playing"),
        (words("skip skips skipping jumps jumping"),
         "audio playback interruption during a track music skips while playing"),
    ), words("app playing play middle midway mid out repeatedly unexpected unexpectedly randomly playback track song audio music player songs tracks")),
    Rule(Intent.APP_TECHNICAL, "app-launch-crash", (words("app application player"), words("launch launching startup start starts starting opening open")), (
        (words("crash crashes crashing"), "application crashes when starting application launch failure"),
        (words("close closes closing exits exit"), "application closes when starting application launch failure"),
    ), words("after immediately repeatedly unexpectedly startup launch launching starts starting start open opening app application player")),
    Rule(Intent.FEATURE, "playlist-sort", (words("playlist playlists"),), (
        (words("sort sorting sorted"), "feature request to sort and reorder tracks in a playlist"),
        (words("reorder reordering rearrange rearranging organise organize"), "feature request to reorder and sort tracks in a playlist"),
    ), words("feature request add want wish like option manually manual songs tracks playlist playlists")),
)


def classify_issue(subject, description, intent, *, public_context=False):
    """Return only generated vocabulary. Unexpected details cause a miss."""
    raw = normalize(f"{subject} {description}").casefold()
    if (public_context or len(raw) > 360 or not re.fullmatch(r"[a-z .,!?'\-]+", raw)
            or NEGATION.search(raw) or context_policy(raw)):
        return None
    tokens = frozenset(re.findall(r"[a-z]+", raw))
    for rule in RULES:
        if intent != rule.intent or not all(tokens & anchor for anchor in rule.anchors):
            continue
        variant_words = frozenset().union(*(vocabulary for vocabulary, _ in rule.variants))
        accepted = COMMON | rule.optional | variant_words | frozenset().union(*rule.anchors)
        if tokens <= accepted:
            for vocabulary, phrase in rule.variants:
                if tokens & vocabulary:
                    return Issue(rule.key, phrase)
    return None


def evidence_key(item):
    return (str(item.article_id), str(item.revision_id), str(item.chunk_id), item.content_hash)


def general_guidance(content, evidence, synthetic):
    if not isinstance(content, str) or len(content) > 1600 or content != content.strip():
        return False
    source = "SYNTHETIC_TEST" if synthetic else "APPROVED_REFERENCE"
    if not evidence or any(item.source_type != source for item in evidence):
        return False
    opening, *lines = content.split("\n")
    catalog = {phrase for phrase, _ in sentences(evidence).values()}
    return (opening == OPENING and bool(lines) and len(lines) == len(set(lines))
            and all(line in catalog and line == scrub(line) and not (
                PRIVATE.search(line) or NEGATION.search(line) or context_policy(line) or UNSAFE.search(line)
                or not re.fullmatch(r"[A-Za-z ,.!?'-]+", line) or re.search(r"\b[A-Z][A-Za-z]+", line[1:])
            ) for line in lines))


def cosine(left, right):
    if len(left) != DIMENSION or len(right) != DIMENSION:
        return None
    if not all(math.isfinite(value) for vector in (left, right) for value in vector):
        return None
    norms = math.hypot(*left) * math.hypot(*right)
    if norms <= 0:
        return None
    return max(-1.0, min(1.0, math.fsum(a * b for a, b in zip(left, right, strict=True)) / norms))


@dataclass(frozen=True)
class Candidate:
    id: object
    version: int
    vector: list[float]
    text: str
    dependencies: tuple


def choose(query, candidates, by_evidence, *, threshold, synthetic):
    if len(candidates) > MAX_CANDIDATES:
        return None, "CAPACITY", 0
    accepted = []
    rejected = 0
    for item in candidates:
        score = cosine(query, item.vector)
        evidence = [by_evidence.get(key) for key in item.dependencies]
        if (score is None or score < threshold or not evidence or None in evidence
                or not general_guidance(item.text, evidence, synthetic)):
            rejected += 1
            continue
        accepted.append((score, item, evidence))
    if not accepted:
        return None, "NO_MATCH", rejected
    if len({(item.text, frozenset(item.dependencies)) for _, item, _ in accepted}) != 1:
        return None, "AMBIGUOUS", rejected + len(accepted)
    return min(accepted, key=lambda row: (-row[0], str(row[1].id))), "HIT", rejected
