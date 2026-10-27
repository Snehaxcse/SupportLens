import pandas as pd
import pytest

from supportlens.ml.data import normalize_text
from supportlens.ml.grouping import ancestry_anchor, group_records, read_parents
from supportlens.ml.io import IntegrityError
from supportlens.ml.split import assert_no_leakage, attach_eligibility, create_split, persist_split
from conftest import annotation


def test_ancestry_and_transitive_duplicate_components():
    # 2 and 3 share ancestry; 3 and 4 share normalized text; 4 and 5 share ancestry.
    records = [annotation("2", "First synthetic problem."), annotation("3", "Shared synthetic text."),
               annotation("4", " SHARED SYNTHETIC TEXT. "), annotation("5", "Last synthetic problem.")]
    frame = pd.DataFrame(records)
    parents = {1: None, 2: 1, 3: 1, 4: None, 5: 4}
    groups, _ = group_records(frame, frame, parents, [])
    assert groups["group_id"].nunique() == 1


def test_unlabeled_corpus_records_bridge_gold_components():
    corpus = pd.DataFrame([annotation("1", "One synthetic input."), annotation("2", "Bridge input."), annotation("3", "Bridge input.")])
    gold = corpus.loc[corpus["thread_id"].isin(("1", "3"))]
    groups, _ = group_records(corpus, gold, {1: None, 2: 1, 3: None}, [])
    assert groups["group_id"].nunique() == 1


def test_near_duplicate_links_join_components():
    frame = pd.DataFrame([annotation("1", "One synthetic problem."), annotation("2", "A distinct synthetic problem.")])
    groups, _ = group_records(frame, frame, {1: None, 2: None}, [["1", "2"]])
    assert groups["group_id"].nunique() == 1


def test_missing_parent_quarantines_entire_duplicate_component():
    frame = pd.DataFrame([annotation("1", "Same synthetic input."), annotation("2", "Same synthetic input.")])
    groups, _ = group_records(frame, frame, {1: None, 2: 999}, [])
    assert groups["group_quarantined"].all()
    assert set(groups["ancestry_status"]) == {"complete", "missing_parent"}


def test_shared_missing_parent_and_cycle_detection():
    cache = {}
    assert ancestry_anchor(1, {1: 99, 2: 99}, cache) == ancestry_anchor(2, {1: 99, 2: 99}, cache)
    assert ancestry_anchor(1, {1: 2, 2: 1}, {})[1] == "cycle"


def test_raw_parent_parser_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("tweet_id,in_response_to_tweet_id\n1,\n1,\n")
    with pytest.raises(IntegrityError, match="Duplicate"):
        read_parents(path)


def test_raw_decimal_ids_are_exact(tmp_path):
    path = tmp_path / "raw.csv"
    path.write_text("tweet_id,in_response_to_tweet_id\n123,\n124,123.0\n")
    assert read_parents(path) == {123: None, 124: 123}


def test_flagged_and_unsupported_rows_retained_but_not_eligible(split_frame):
    frame = split_frame.drop(columns=["group_id", "ancestry_status", "group_quarantined", "corpus_component_size", "training_eligible", "exclusion_reasons"])
    frame.loc[0, "needs_context"] = True
    frame.loc[1, "language_issue"] = True
    frame.loc[2, "intent"] = "UNKNOWN"
    groups = split_frame[["thread_id", "group_id", "ancestry_status", "group_quarantined", "corpus_component_size"]]
    result = attach_eligibility(frame, groups)
    marked = result.set_index("thread_id")
    assert len(result) == len(frame)
    for tid in frame.loc[:2, "thread_id"]:
        assert not marked.loc[tid, "training_eligible"]


def test_deterministic_persisted_split_and_no_overlap(split_frame, tmp_path):
    first, manifest = create_split(split_frame, {"synthetic": True})
    second, repeated = create_split(split_frame.sample(frac=1, random_state=7), {"synthetic": True})
    assert manifest == repeated
    assert_no_leakage(first)
    persist_split(tmp_path, manifest)
    persist_split(tmp_path, repeated)
    changed = {**manifest, "records": []}
    with pytest.raises(IntegrityError, match="differs"):
        persist_split(tmp_path, changed)
    assert set(first["split"]) == {"train", "validation", "test"}


def test_transitive_group_kept_together_in_split(split_frame):
    split_frame.loc[:2, "group_id"] = "connected"
    assigned, _ = create_split(split_frame, {})
    assert assigned.loc[assigned["group_id"].eq("connected"), "split"].nunique() == 1


def test_cross_partition_group_is_release_failure(assigned_frame):
    a = assigned_frame.index[assigned_frame["split"].eq("train")][0]
    b = assigned_frame.index[assigned_frame["split"].eq("test")][0]
    assigned_frame.loc[b, "group_id"] = assigned_frame.loc[a, "group_id"]
    with pytest.raises(IntegrityError, match="component"):
        assert_no_leakage(assigned_frame)


def test_cross_partition_normalized_duplicate_is_release_failure(assigned_frame):
    a = assigned_frame.index[assigned_frame["split"].eq("train")][0]
    b = assigned_frame.index[assigned_frame["split"].eq("test")][0]
    assigned_frame.loc[b, "initial_customer_message"] = assigned_frame.loc[a, "initial_customer_message"].upper()
    with pytest.raises(IntegrityError, match="duplicate"):
        assert_no_leakage(assigned_frame)


def test_insufficient_support_blocks_instead_of_fabricating(split_frame):
    with pytest.raises(IntegrityError, match="support"):
        create_split(split_frame.iloc[:10], {})


def test_quarantine_never_enters_primary_split(split_frame):
    split_frame.loc[0, "group_quarantined"] = True
    split_frame.loc[0, "training_eligible"] = False
    assigned, _ = create_split(split_frame, {})
    assert assigned.set_index("thread_id").loc[split_frame.iloc[0]["thread_id"], "split"] == "quarantine"
