import json

import joblib
import pytest
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import Pipeline

from supportlens.ml.config import CORE_LABELS
from supportlens.ml.inference import IntentClassifier, ModelArtifactError, validate_message
from supportlens.ml.io import IntegrityError, read_json, sha256
from supportlens.ml.train import fit_candidates, train_and_evaluate, training_metadata


def test_tfidf_candidates_fit_train_only(assigned_frame, monkeypatch):
    observed = []
    original = TfidfVectorizer.fit_transform
    def capture(self, documents, y=None):
        observed.append(list(documents))
        return original(self, documents, y)
    monkeypatch.setattr(TfidfVectorizer, "fit_transform", capture)
    _, selected, _ = fit_candidates(assigned_frame)
    train_inputs = set(assigned_frame.loc[assigned_frame["split"].eq("train"), "initial_customer_message"])
    assert len(observed) == 2
    assert all(set(documents) == train_inputs for documents in observed)
    for row in assigned_frame.loc[assigned_frame["split"].ne("train")].itertuples():
        assert "unique_" + row.thread_id not in selected.named_steps["tfidf"].vocabulary_


def test_train_evaluate_once_and_load_artifact(assigned_frame, tmp_path, monkeypatch):
    model_dir, report_dir = tmp_path / "model", tmp_path / "evaluation"
    test_inputs = assigned_frame.loc[assigned_frame["split"].eq("test"), "initial_customer_message"].tolist()
    calls = []
    original = Pipeline.predict_proba
    def observe(self, documents, **params):
        if list(documents) == test_inputs:
            calls.append(1)
        return original(self, documents, **params)
    monkeypatch.setattr(Pipeline, "predict_proba", observe)
    result = train_and_evaluate(assigned_frame, {"metadata": {"synthetic": True}}, model_dir, report_dir, {"synthetic": True})
    assert len(calls) == 1
    assert result["test"]["tfidf_logistic_regression"]["samples"] == len(test_inputs)
    model = joblib.load(model_dir / "model.joblib")
    for row in assigned_frame.loc[assigned_frame["split"].eq("test")].itertuples():
        assert "unique_" + row.thread_id not in model.named_steps["tfidf"].vocabulary_
    assert IntentClassifier(model_dir).predict("Synthetic playback failure.")["requires_human_review"]
    repeated = train_and_evaluate(assigned_frame, {"metadata": {"synthetic": True}}, model_dir, report_dir, {"synthetic": True})
    assert result == repeated
    assert len(calls) == 1
    with pytest.raises(IntegrityError, match="differs"):
        train_and_evaluate(assigned_frame, {"metadata": {}}, model_dir, report_dir, {"changed": True})
    assert "Synthetic playback" not in (report_dir / "report.md").read_text()


@pytest.mark.parametrize("message", [None, 9, [], {}, "", " \n ", "a" * 10001])
def test_inference_rejects_invalid_input(message):
    with pytest.raises(ValueError):
        validate_message(message)


def test_human_review_even_for_high_probability(local_model):
    classifier = IntentClassifier(local_model)
    result = classifier.predict("Synthetic issue ACCOUNT_ACCESS")
    assert result["predicted_intent"] in CORE_LABELS
    assert result["requires_human_review"] is True
    assert result["automated_actions_allowed"] is False
    assert result["abstained_from_automated_handling"] is True
    assert result["score_type"] == "UNCALIBRATED_RAW_PROBABILITY"
    assert set(result["class_scores"]) == set(CORE_LABELS)
    assert sum(result["class_scores"].values()) == pytest.approx(1)
    assert set(result["unsupported_labels"]) == {"UNKNOWN", "NON_SUPPORT", "OTHER_SUPPORT"}


def test_missing_model_fails_closed(tmp_path):
    with pytest.raises(ModelArtifactError):
        IntentClassifier(tmp_path / "missing")


def test_corrupt_binary_rejected_before_deserialization(local_model, monkeypatch):
    (local_model / "model.joblib").write_bytes(b"corrupt")
    def should_never_load(*args):
        pytest.fail("Corrupt artifact reached deserialization")
    monkeypatch.setattr(joblib, "load", should_never_load)
    with pytest.raises(ModelArtifactError):
        IntentClassifier(local_model)


def test_unreadable_serialization_even_with_matching_hash(local_model):
    (local_model / "model.joblib").write_bytes(b"not a model")
    metadata = read_json(local_model / "metadata.json")
    metadata["artifact_sha256"] = sha256(local_model / "model.joblib")
    (local_model / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ModelArtifactError):
        IntentClassifier(local_model)


def test_invalid_metadata_safety_contract(local_model):
    metadata = read_json(local_model / "metadata.json")
    metadata["automated_actions_allowed"] = True
    (local_model / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ModelArtifactError):
        IntentClassifier(local_model)


@pytest.mark.parametrize("field", ["model_version", "data_version", "split_version", "unsupported_labels", "features"])
def test_incomplete_metadata_rejected_at_load(local_model, field):
    metadata = read_json(local_model / "metadata.json")
    del metadata[field]
    (local_model / "metadata.json").write_text(json.dumps(metadata))
    with pytest.raises(ModelArtifactError):
        IntentClassifier(local_model)


def test_reproducible_model_metadata(local_model):
    model = joblib.load(local_model / "model.joblib")
    first = training_metadata({"source_hash": "synthetic"}, 1.0, sha256(local_model / "model.joblib"), model.classes_.tolist())
    second = training_metadata({"source_hash": "synthetic"}, 1.0, sha256(local_model / "model.joblib"), model.classes_.tolist())
    assert first == second
    assert first["features"]["input_column"] == "initial_customer_message"
    assert first["library_versions"]["scikit-learn"] == "1.7.2"
