from pathlib import Path

import pandas as pd
import pytest

from supportlens.ml.config import GOLD_SHA256
from supportlens.ml.data import model_inputs, normalize_flag, read_csv_strings, validate_and_derive
from supportlens.ml.io import IntegrityError, sha256
from conftest import annotation


def derive(frame, source=None):
    return validate_and_derive(frame, frame.copy() if source is None else source, "synthetic-source-hash")


def test_required_schema(gold_factory):
    frame = gold_factory().drop(columns="intent")
    with pytest.raises(IntegrityError, match="columns"):
        derive(frame)


@pytest.mark.parametrize("label", ["BAD", "", "account_access", "ACCOUNT_ACCESS "])
def test_invalid_labels(gold_factory, label):
    frame = gold_factory(annotation(intent=label))
    with pytest.raises(IntegrityError, match="label"):
        derive(frame)


@pytest.mark.parametrize("text", ["", "  ", "\n\t"])
def test_blank_initial_input(gold_factory, text):
    with pytest.raises(IntegrityError, match="Blank"):
        derive(gold_factory(annotation(initial_customer_message=text)))


def test_duplicate_identifiers(gold_factory):
    with pytest.raises(IntegrityError, match="Duplicate"):
        derive(gold_factory(annotation(), annotation()))


@pytest.mark.parametrize("value,expected", [("TRUE", True), ("false", False), (" False ", False), (True, True)])
def test_flag_normalization(value, expected):
    assert normalize_flag(value) is expected


@pytest.mark.parametrize("value", ["yes", "", "1", 1, None])
def test_invalid_flags_are_not_silently_false(value):
    with pytest.raises(IntegrityError):
        normalize_flag(value)


def test_invalid_provenance(gold_factory):
    with pytest.raises(IntegrityError, match="provenance"):
        derive(gold_factory(annotation(annotation_source="UNREVIEWED")))


def test_correction_is_stable_source_backed_and_non_destructive(gold_factory, tmp_path):
    source = gold_factory(annotation(tid="99"))
    original = source.copy(deep=True)
    original.loc[0, ["initial_customer_message", "customer_context"]] = "#NAME?"
    path = tmp_path / "gold.csv"
    original.to_csv(path, index=False)
    before = sha256(path)
    repaired, report = derive(read_csv_strings(path), source)
    again, report_again = derive(read_csv_strings(path), source)
    assert sha256(path) == before
    assert original.iloc[0]["initial_customer_message"] == "#NAME?"
    assert repaired.iloc[0]["initial_customer_message"] == source.iloc[0]["initial_customer_message"]
    assert repaired.iloc[0]["intent"] == original.iloc[0]["intent"]
    assert report == report_again
    pd.testing.assert_frame_equal(repaired, again)
    assert report["corrections"][0]["thread_id"] == "99"
    assert report["corrections"][0]["annotation_fields_unchanged"]
    assert "Synthetic customer" not in str(report)


def test_unmatched_damaged_record_explicitly_excluded(gold_factory):
    damaged = gold_factory(annotation(initial_customer_message="#NAME?", customer_context="#NAME?"))
    source = gold_factory(annotation(tid="2"))
    frame, report = derive(damaged, source)
    assert frame.iloc[0]["correction_status"] == "excluded_unmatched_source"
    assert report["exclusions"][0]["reason"] == "DAMAGED_UNMATCHED_SOURCE"


def test_unexplained_source_difference_blocks(gold_factory):
    frame = gold_factory()
    source = gold_factory(annotation(initial_customer_message="Different synthetic source."))
    with pytest.raises(IntegrityError, match="mismatch"):
        derive(frame, source)


def test_annotation_types(gold_factory):
    with pytest.raises(IntegrityError, match="CSV strings"):
        derive(gold_factory(annotation(intent=7)))


def test_duplicate_and_near_duplicate_detection(gold_factory):
    text = "Synthetic music playback stops whenever the screen locks."
    frame = gold_factory(annotation("1", text), annotation("2", text.upper()), annotation("3", text + "!"))
    _, report = derive(frame)
    assert report["duplicates"]["normalized_initial_excess_rows"] == 1
    assert report["duplicates"]["near_duplicate_pairs"] >= 1


def test_only_initial_message_is_exposed_to_features(gold_factory):
    frame = gold_factory(annotation(conversation="AGENT_ONLY_SENTINEL", customer_context="FUTURE_CUSTOMER_SENTINEL", annotation_notes="PRIVATE_METADATA_SENTINEL"))
    inputs = model_inputs(frame)
    assert inputs == [frame.iloc[0]["initial_customer_message"]]
    assert all("SENTINEL" not in value for value in inputs)


@pytest.mark.parametrize("payload", ["thread_id,thread_id\n1,2\n", "thread_id,intent\n1,ACCOUNT_ACCESS,EXTRA\n"])
def test_malformed_csv_rejected(tmp_path, payload):
    file = tmp_path / "malformed.csv"
    file.write_text(payload)
    with pytest.raises(IntegrityError):
        read_csv_strings(file)


def test_real_gold_matches_approved_immutable_source():
    root = Path(__file__).resolve().parents[2]
    if not (root / "data/processed/gold_annotations.csv").exists():
        pytest.skip("Optional private source is not distributed with the public code.")
    assert sha256(root / "data/processed/gold_annotations.csv") == GOLD_SHA256
