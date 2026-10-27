"""Versioned M1 policy and paths; no environment or secret loading."""
from pathlib import Path

CANONICAL_LABELS = (
    "ACCOUNT_ACCESS", "ACCOUNT_SECURITY", "BILLING_PAYMENT", "SUBSCRIPTION",
    "APP_TECHNICAL", "CONTENT", "FEATURE", "OTHER_SUPPORT", "NON_SUPPORT", "UNKNOWN",
)
CORE_LABELS = CANONICAL_LABELS[:7]
GOLD_SHA256 = "81d3ed3eb4a83ccc39cf25148a3e7a791038ad78eff0b0c9fd2b591874c9cc8a"
REQUIRED_COLUMNS = (
    "thread_id", "initial_customer_message", "customer_context", "conversation",
    "discovery_label", "intent", "needs_context", "language_issue",
    "annotation_notes", "annotation_source",
)
TEXT_COLUMNS = ("initial_customer_message", "customer_context", "conversation")
ANNOTATION_COLUMNS = (
    "discovery_label", "intent", "needs_context", "language_issue",
    "annotation_notes", "annotation_source",
)
SOURCES = ("EARLIER_GOLD", "NON_SUPPORT_HEURISTIC", "RANDOM_TAXONOMY_CHECK")
EXCEL_ERRORS = frozenset({
    "#NAME?", "#VALUE!", "#REF!", "#DIV/0!", "#N/A", "#NUM!", "#NULL!",
    "#SPILL!", "#CALC!",
})
SEED = 42
DATA_VERSION = "m1_v1"
SPLIT_VERSION = "m1_split_v1"
MODEL_VERSION = "m1_tfidf_lr_v1"
GROUPING_VERSION = "ancestry_normalized_initial_components_v1"
SPLIT_ALGORITHM = "deterministic_group_search_v1"
NEAR_DUPLICATE_THRESHOLD = 0.92
MAX_INPUT_CHARACTERS = 10_000


def paths(root: Path) -> dict[str, Path]:
    return {
        "raw": root / "data/raw/twcs.csv",
        "gold": root / "data/processed/gold_annotations.csv",
        "threads": root / "data/processed/spotify_threads.csv",
        "derived": root / "data/derived" / DATA_VERSION,
        "splits": root / "data/splits" / SPLIT_VERSION,
        "models": root / "artifacts/models" / MODEL_VERSION,
        "evaluation": root / "evaluation" / DATA_VERSION,
    }
