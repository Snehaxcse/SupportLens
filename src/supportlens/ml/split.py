"""Seeded group-level split search; membership files are write-once."""
import numpy as np
import pandas as pd

from .config import CORE_LABELS, SEED, SPLIT_ALGORITHM, SPLIT_VERSION
from .data import normalize_text
from .io import IntegrityError, digest_json, save_json_once

PROPORTIONS = (0.70, 0.15, 0.15)
PARTITIONS = ("train", "validation", "test")


def attach_eligibility(derived: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    frame = derived.merge(groups, on="thread_id", how="left", validate="one_to_one")
    if frame["group_id"].isna().any():
        raise IntegrityError("Grouping membership is incomplete.")
    reasons = []
    for row in frame.itertuples():
        reason = []
        if row.intent not in CORE_LABELS:
            reason.append("OUTSIDE_CORE_LABELS")
        if row.needs_context:
            reason.append("NEEDS_CONTEXT")
        if row.language_issue:
            reason.append("LANGUAGE_ISSUE")
        if row.correction_status.startswith("excluded"):
            reason.append("DAMAGED_UNRESOLVED")
        if row.group_quarantined:
            reason.append("INCOMPLETE_ANCESTRY_COMPONENT")
        reasons.append(";".join(reason))
    frame["exclusion_reasons"] = reasons
    frame["training_eligible"] = frame["exclusion_reasons"].eq("")
    return frame.sort_values("thread_id").reset_index(drop=True)


def assert_no_leakage(frame: pd.DataFrame) -> None:
    if frame["thread_id"].duplicated().any() or frame["split"].isna().any():
        raise IntegrityError("Split membership is incomplete or duplicated.")
    if not frame["split"].isin((*PARTITIONS, "quarantine")).all():
        raise IntegrityError("Unexpected split name.")
    if frame.groupby("group_id")["split"].nunique().gt(1).any():
        raise IntegrityError("A connected component crosses partitions.")
    normalized = frame["initial_customer_message"].map(normalize_text)
    # Unresolved damaged placeholders are quarantined and not valid duplicate text.
    valid = ~frame["correction_status"].str.startswith("excluded")
    if frame.loc[valid].groupby(normalized[valid])["split"].nunique().gt(1).any():
        raise IntegrityError("Normalized duplicate input crosses partitions.")
    if frame.loc[frame["group_quarantined"], "split"].ne("quarantine").any():
        raise IntegrityError("Incomplete ancestry escaped quarantine.")
    for partition, minimum in (("train", 10), ("validation", 3), ("test", 3)):
        counts = frame.loc[frame["training_eligible"] & frame["split"].eq(partition), "intent"].value_counts()
        if any(counts.get(label, 0) < minimum for label in CORE_LABELS):
            raise IntegrityError("Insufficient per-class support for a valid preliminary split.")


def create_split(frame: pd.DataFrame, source_metadata: dict, seed: int = SEED) -> tuple[pd.DataFrame, dict]:
    frame = frame.copy().sort_values("thread_id").reset_index(drop=True)
    groups = sorted(frame.loc[~frame["group_quarantined"], "group_id"].unique())
    if len(groups) < 3:
        raise IntegrityError("Too few complete groups for train/validation/test.")
    eligible = frame.loc[frame["training_eligible"]]
    counts = pd.crosstab(eligible["group_id"], eligible["intent"]).reindex(index=groups, columns=CORE_LABELS, fill_value=0).fillna(0).to_numpy(dtype=int)
    total = counts.sum(axis=0)
    if (total < 16).any():
        raise IntegrityError("A class cannot meet train/validation/test support gates.")
    group_rows = frame.loc[~frame["group_quarantined"]].groupby("group_id").size().reindex(groups).to_numpy()
    n_train = round(len(groups) * PROPORTIONS[0])
    n_validation = round(len(groups) * PROPORTIONS[1])
    rng = np.random.default_rng(seed)
    best = None
    best_score = float("inf")
    for _ in range(4096):
        order = rng.permutation(len(groups))
        parts = (order[:n_train], order[n_train:n_train + n_validation], order[n_train + n_validation:])
        class_counts = np.array([counts[p].sum(axis=0) for p in parts])
        if (class_counts[0] < 10).any() or (class_counts[1:] < 3).any():
            continue
        target = np.array(PROPORTIONS)[:, None] * total
        score = float((((class_counts - target) / np.maximum(target, 1)) ** 2).mean())
        row_fractions = np.array([group_rows[p].sum() for p in parts]) / group_rows.sum()
        score += float(((row_fractions - PROPORTIONS) ** 2).sum())
        if score < best_score:
            best_score, best = score, parts
    if best is None:
        raise IntegrityError("No leakage-safe split met class support gates; do not train.")
    assignments = {groups[int(g)]: name for name, part in zip(PARTITIONS, best) for g in part}
    frame["split"] = frame["group_id"].map(assignments).fillna("quarantine")
    assert_no_leakage(frame)
    columns = ["thread_id", "group_id", "split", "intent", "training_eligible", "exclusion_reasons", "needs_context", "language_issue", "ancestry_status", "group_quarantined"]
    records = frame[columns].to_dict(orient="records")
    metadata = {
        "version": SPLIT_VERSION, "algorithm": SPLIT_ALGORITHM, "seed": seed,
        "target_proportions": dict(zip(PARTITIONS, PROPORTIONS)),
        "search_candidates": 4096, "selection_uses": "Label counts/group sizes only, never model scores or text features.",
        "minimum_primary_support": {"train": 10, "validation": 3, "test": 3},
        "source_metadata": source_metadata, "membership_sha256": digest_json(records),
        "rows": frame.groupby("split").size().to_dict(),
        "groups": frame.groupby("split")["group_id"].nunique().to_dict(),
        "primary_counts": {name: frame.loc[frame["training_eligible"] & frame["split"].eq(name), "intent"].value_counts().reindex(CORE_LABELS, fill_value=0).to_dict() for name in PARTITIONS},
        "leakage_checks": {"component_overlap": 0, "normalized_input_overlap": 0, "incomplete_ancestry_in_primary": 0},
        "diagnostic_policy": "All records share group assignments. Only test-group diagnostics are held out from final development fitting; train-group diagnostics are not independent.",
        "preliminary": True,
    }
    return frame, {"metadata": metadata, "records": records}


def persist_split(directory, manifest: dict) -> None:
    # Comparing the full manifest prevents silent changes to IDs, groups, flags or sources.
    save_json_once(directory / "membership.json", manifest)
