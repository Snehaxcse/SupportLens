"""Connected components across raw ancestry and normalized initial messages."""
import csv
import re
from collections import defaultdict
from pathlib import Path

import pandas as pd

from .config import GROUPING_VERSION
from .data import normalize_text, validate_ids
from .io import IntegrityError, digest_json


class Components:
    def __init__(self, members):
        self.parent = {member: member for member in members}

    def find(self, member):
        root = member
        while self.parent[root] != root:
            root = self.parent[root]
        while member != root:
            parent = self.parent[member]
            self.parent[member] = root
            member = parent
        return root

    def union(self, left, right):
        a, b = self.find(left), self.find(right)
        if a != b:
            first, second = sorted((a, b))
            self.parent[second] = first


def raw_identifier(value: str) -> int:
    # CSV parent IDs may be represented as integer-valued decimal strings.
    if not re.fullmatch(r"[1-9]\d*(?:\.0+)?", value):
        raise IntegrityError("Malformed raw conversation identifier.")
    return int(value.split(".")[0])


def read_parents(raw_path: Path) -> dict[int, int | None]:
    parents = {}
    try:
        with raw_path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not {"tweet_id", "in_response_to_tweet_id"}.issubset(reader.fieldnames or []):
                raise IntegrityError("Raw ancestry columns are absent.")
            for row in reader:
                tid = raw_identifier(row["tweet_id"])
                if tid in parents:
                    raise IntegrityError("Duplicate raw conversation identifier.")
                parent = row["in_response_to_tweet_id"]
                parents[tid] = raw_identifier(parent) if parent else None
    except (OSError, KeyError, TypeError) as exc:
        raise IntegrityError("Raw ancestry could not be read safely.") from exc
    return parents


def ancestry_anchor(tid: int, parents: dict, cache: dict) -> tuple[str, str]:
    chain = []
    positions = set()
    current = tid
    while True:
        if current in cache:
            anchor, status = cache[current]
            break
        if current in positions:
            anchor, status = "cycle:" + str(min(chain)), "cycle"
            break
        if current not in parents:
            # Include the absent parent identity to join separate visible branches.
            anchor, status = "missing:" + str(current), "missing_parent"
            break
        positions.add(current)
        chain.append(current)
        if parents[current] is None:
            anchor, status = "root:" + str(current), "complete"
            break
        current = parents[current]
    for member in chain:
        cache[member] = (anchor, status)
    return anchor, status


def group_records(threads: pd.DataFrame, gold: pd.DataFrame, parents: dict,
                  near_links: list[list[str]]) -> tuple[pd.DataFrame, dict]:
    """Use the full unlabeled thread corpus as bridges, including diagnostic gold."""
    validate_ids(threads)
    validate_ids(gold)
    universe = threads[["thread_id", "initial_customer_message"]].copy()
    known = set(universe["thread_id"])
    missing_gold = gold.loc[~gold["thread_id"].isin(known), ["thread_id", "initial_customer_message"]]
    universe = pd.concat([universe, missing_gold], ignore_index=True).sort_values("thread_id")
    ids = universe["thread_id"].tolist()
    components = Components(ids)
    anchors = {}
    normalized = {}
    status_by_id = {}
    cache = {}
    ancestry_edges = duplicate_edges = 0
    for row in universe.itertuples(index=False):
        anchor, status = ancestry_anchor(int(row.thread_id), parents, cache)
        status_by_id[row.thread_id] = status
        if anchor in anchors:
            components.union(row.thread_id, anchors[anchor])
            ancestry_edges += 1
        else:
            anchors[anchor] = row.thread_id
        text_key = normalize_text(row.initial_customer_message)
        if not text_key:
            raise IntegrityError("Empty normalized text cannot establish a duplicate group.")
        if text_key in normalized:
            components.union(row.thread_id, normalized[text_key])
            duplicate_edges += 1
        else:
            normalized[text_key] = row.thread_id
    for left, right in near_links:
        if left not in components.parent or right not in components.parent:
            raise IntegrityError("Near-duplicate link references an absent record.")
        components.union(left, right)
    groups = defaultdict(list)
    for tid in ids:
        groups[components.find(tid)].append(tid)
    group_ids = {key: "group_" + digest_json(sorted(members))[:24] for key, members in groups.items()}
    incomplete = {key: any(status_by_id[tid] != "complete" for tid in members) for key, members in groups.items()}
    memberships = []
    for tid in sorted(gold["thread_id"]):
        key = components.find(tid)
        memberships.append({
            "thread_id": tid, "group_id": group_ids[key],
            "ancestry_status": status_by_id[tid],
            "group_quarantined": incomplete[key],
            "corpus_component_size": len(groups[key]),
        })
    frame = pd.DataFrame(memberships)
    report = {
        "version": GROUPING_VERSION, "corpus_records": len(universe),
        "corpus_components": len(groups), "gold_groups": int(frame["group_id"].nunique()),
        "ancestry_link_count": ancestry_edges, "normalized_duplicate_link_count": duplicate_edges,
        "near_duplicate_link_count": len(near_links),
        "gold_ancestry_status": frame["ancestry_status"].value_counts().sort_index().to_dict(),
        "quarantined_gold_rows": int(frame["group_quarantined"].sum()),
        "quarantined_gold_groups": int(frame.loc[frame["group_quarantined"], "group_id"].nunique()),
        "policy": "Quarantine the entire connected component if any corpus member has missing ancestry or a cycle; never train or score these as held out.",
        "duplicate_policy": "NFKC/case/whitespace-normalized initial text across all corpus records; near links among gold; transitive closure with ancestry.",
    }
    return frame, report
