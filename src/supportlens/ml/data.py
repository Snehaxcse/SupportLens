"""Strict annotation validation and non-destructive source-backed correction."""
import csv
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from .config import (
    ANNOTATION_COLUMNS, CANONICAL_LABELS, CORE_LABELS, EXCEL_ERRORS,
    NEAR_DUPLICATE_THRESHOLD, REQUIRED_COLUMNS, SOURCES, TEXT_COLUMNS,
)
from .io import IntegrityError


def read_csv_strings(path: Path) -> pd.DataFrame:
    """Keep spreadsheet errors and literal NA strings visible to validation."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            header = next(reader)
            if len(set(header)) != len(header):
                raise IntegrityError("Duplicate CSV column names.")
            if any(len(row) != len(header) for row in reader):
                raise IntegrityError("Malformed CSV record width.")
        return pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    except (OSError, StopIteration, pd.errors.ParserError, UnicodeError) as exc:
        raise IntegrityError("Missing or unreadable dataset.") from exc


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip().casefold()


def normalize_flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().casefold() in {"true", "false"}:
        return value.strip().casefold() == "true"
    raise IntegrityError("Annotation flag must be a true/false value.")


def require_columns(frame: pd.DataFrame, columns: tuple[str, ...]) -> None:
    if not set(columns).issubset(frame.columns):
        raise IntegrityError("Required dataset columns are missing.")


def validate_ids(frame: pd.DataFrame) -> None:
    if not frame["thread_id"].map(lambda x: isinstance(x, str) and bool(re.fullmatch(r"[1-9]\d*", x))).all():
        raise IntegrityError("Thread identifiers must be positive integer strings.")
    if frame["thread_id"].duplicated().any():
        raise IntegrityError("Duplicate thread identifiers.")


def duplicate_relationships(frame: pd.DataFrame) -> tuple[list[list[str]], dict]:
    """Private pair IDs only; no messages in the machine-readable report."""
    normalized = [normalize_text(t) for t in frame["initial_customer_message"]]
    ids = frame["thread_id"].tolist()
    pairs = []
    for i, left in enumerate(normalized):
        for j in range(i + 1, len(normalized)):
            right = normalized[j]
            if left == right:
                continue
            # Short generic phrases are handled as exact duplicates only.
            if min(len(left), len(right)) < 20:
                continue
            if 2 * min(len(left), len(right)) / (len(left) + len(right)) < NEAR_DUPLICATE_THRESHOLD:
                continue
            if SequenceMatcher(None, left, right, autojunk=False).ratio() >= NEAR_DUPLICATE_THRESHOLD:
                pairs.append(sorted([ids[i], ids[j]]))
    counts = Counter(normalized)
    return sorted(pairs), {
        "exact_initial_excess_rows": int(frame["initial_customer_message"].duplicated().sum()),
        "normalized_initial_excess_rows": len(normalized) - len(counts),
        "normalized_duplicate_groups": sum(n > 1 for n in counts.values()),
        "near_duplicate_pairs": len(pairs),
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        "near_duplicate_policy": "Conservatively connect reviewed gold pairs; not exhaustive semantic deduplication.",
    }


def validate_and_derive(gold: pd.DataFrame, threads: pd.DataFrame, gold_hash: str) -> tuple[pd.DataFrame, dict]:
    require_columns(gold, REQUIRED_COLUMNS)
    require_columns(threads, ("thread_id", *TEXT_COLUMNS))
    if gold.empty or threads.empty:
        raise IntegrityError("Datasets must not be empty.")
    validate_ids(gold)
    validate_ids(threads)
    for column in REQUIRED_COLUMNS:
        if not gold[column].map(lambda value: isinstance(value, str)).all():
            raise IntegrityError("Gold fields must be CSV strings before normalization.")
    if not gold["intent"].isin(CANONICAL_LABELS).all():
        raise IntegrityError("Noncanonical or missing intent label.")
    if not gold["annotation_source"].isin(SOURCES).all():
        raise IntegrityError("Noncanonical or missing annotation provenance.")
    for column in TEXT_COLUMNS:
        if not gold[column].str.strip().ne("").all():
            raise IntegrityError("Blank gold input or conversation.")
        if not threads[column].map(lambda v: isinstance(v, str) and bool(v.strip())).all():
            raise IntegrityError("Blank or invalid reconstructed source text.")
    result = gold.copy(deep=True).sort_values("thread_id").reset_index(drop=True)
    for column in ("needs_context", "language_issue"):
        result[column] = result[column].map(normalize_flag)
    result["correction_status"] = "unchanged"
    source = threads.set_index("thread_id")
    corrections = []
    exclusions = []
    for idx, row in result.iterrows():
        damaged = [c for c in TEXT_COLUMNS if row[c].strip().upper() in EXCEL_ERRORS]
        if row["thread_id"] not in source.index:
            if not damaged:
                raise IntegrityError("Gold record is absent from reconstructed sources.")
            result.at[idx, "correction_status"] = "excluded_unmatched_source"
            exclusions.append({"thread_id": row["thread_id"], "reason": "DAMAGED_UNMATCHED_SOURCE"})
            continue
        original = source.loc[row["thread_id"]]
        mismatches = [c for c in TEXT_COLUMNS if row[c] != original[c]]
        if any(c not in damaged for c in mismatches):
            raise IntegrityError("Unexplained gold/source text mismatch; manual review required.")
        if damaged:
            if "conversation" in damaged or row["conversation"] != original["conversation"]:
                result.at[idx, "correction_status"] = "excluded_unmatched_source"
                exclusions.append({"thread_id": row["thread_id"], "reason": "DAMAGED_AMBIGUOUS_SOURCE"})
                continue
            if any(original[c].strip().upper() in EXCEL_ERRORS or not original[c].strip() for c in damaged):
                raise IntegrityError("Reconstructed correction source is also damaged.")
            before_annotations = result.loc[idx, list(ANNOTATION_COLUMNS)].copy()
            for column in damaged:
                result.at[idx, column] = original[column]
            if not before_annotations.equals(result.loc[idx, list(ANNOTATION_COLUMNS)]):
                raise IntegrityError("Correction changed annotation metadata.")
            result.at[idx, "correction_status"] = "repaired_from_source"
            corrections.append({
                "thread_id": row["thread_id"], "original_gold_sha256": gold_hash,
                "changed_fields": damaged, "source": "data/processed/spotify_threads.csv",
                "source_join_key": "thread_id", "reason": "SPREADSHEET_ERROR_SOURCE_MATCH",
                "annotation_fields_unchanged": True,
            })
    pairs, duplicate_report = duplicate_relationships(result)
    result["core_intent"] = result["intent"].isin(CORE_LABELS)
    # Exact and near duplicates are grouped later, never silently removed.
    report = {
        "status": "PASS", "original_gold_sha256": gold_hash,
        "rows": len(result), "canonical_labels": list(CANONICAL_LABELS),
        "label_counts": result["intent"].value_counts().sort_index().to_dict(),
        "blank_optional_fields": {c: int(gold[c].str.strip().eq("").sum()) for c in ("discovery_label", "annotation_notes")},
        "flag_counts": {c: int(result[c].sum()) for c in ("needs_context", "language_issue")},
        "provenance_counts": result["annotation_source"].value_counts().sort_index().to_dict(),
        "duplicates": duplicate_report, "near_duplicate_links": pairs,
        "corrections": corrections, "exclusions": exclusions,
        "source_consistency": "Every source mismatch must be a recorded correction or exclusion.",
        "privacy": "Contains stable record identifiers, never customer text. Keep local and ignored.",
    }
    return result, report


def model_inputs(frame: pd.DataFrame) -> list[str]:
    """The sole feature boundary: no dataframe-wide vectorization is permitted."""
    require_columns(frame, ("initial_customer_message",))
    values = frame["initial_customer_message"].tolist()
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise IntegrityError("Model input must contain nonblank initial messages only.")
    return values
