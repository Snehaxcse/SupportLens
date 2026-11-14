"""Trusted-byte loading, preserved M1 semantics, optional failures and bounded inputs."""
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest

from supportlens.backend.classification.constants import CORE_LABELS, MODEL_DIRECTORY, MODEL_VERSION, SCORE_TYPE, TRUSTED_SHA256
from supportlens.backend.classification.inference import InferenceService, ModelUnavailable, _load_verified_bundle, compose_ticket
from supportlens.backend.classification.schemas import ClassificationState, FailureCategory


def prediction(label="FEATURE", confidence=0.7):
    scores = {name: (confidence if name == label else (1-confidence)/6) for name in CORE_LABELS}
    return {"predicted_intent": label, "confidence": confidence, "class_scores": scores,
        "model_version": MODEL_VERSION, "artifact_sha256": TRUSTED_SHA256["model.joblib"], "score_type": SCORE_TYPE,
        "requires_human_review": True, "automated_actions_allowed": False}


@pytest.fixture
def bundle(tmp_path):
    for name in TRUSTED_SHA256:
        (tmp_path/name).write_bytes((MODEL_DIRECTORY/name).read_bytes())
    return tmp_path, dict(TRUSTED_SHA256)


def change_metadata(bundle, edit):
    directory, expected = bundle
    metadata = json.loads((directory/"metadata.json").read_text())
    edit(metadata)
    (directory/"metadata.json").write_text(json.dumps(metadata))
    expected["metadata.json"] = hashlib.sha256((directory/"metadata.json").read_bytes()).hexdigest()
    completed = json.loads((directory/"completed_run.json").read_text())
    completed["model_files"]["metadata.json"] = expected["metadata.json"]
    (directory/"completed_run.json").write_text(json.dumps(completed))
    expected["completed_run.json"] = hashlib.sha256((directory/"completed_run.json").read_bytes()).hexdigest()


def test_real_trusted_model_is_deterministic_and_m1_compatible():
    service = InferenceService()
    first = service.classify("Synthetic playback", "Synthetic music pauses on a test device.")
    second = service.classify("Synthetic playback", "Synthetic music pauses on a test device.")
    assert first.status == second.status == ClassificationState.SUCCEEDED
    assert first.predicted_intent == second.predicted_intent
    assert first.confidence == second.confidence
    assert first.class_scores == second.class_scores
    assert first.artifact_verified and first.requires_human_review and not first.automated_actions_allowed
    assert first.duration_ms >= 0 and second.duration_ms >= 0


def test_missing_model_returns_unavailable_without_raw_error(tmp_path):
    service = InferenceService(loader=lambda: _load_verified_bundle(tmp_path, TRUSTED_SHA256))
    outcome = service.classify("Synthetic", "Synthetic description")
    assert outcome.status == ClassificationState.UNAVAILABLE and outcome.failure_category == FailureCategory.MODEL_MISSING
    assert str(tmp_path) not in outcome.model_dump_json()


@pytest.mark.parametrize("name", list(TRUSTED_SHA256))
def test_corrupt_files_rejected_before_deserialization(bundle, monkeypatch, name):
    import joblib
    directory, expected = bundle
    (directory/name).write_bytes(b"untrusted synthetic bytes")
    loader = Mock(side_effect=AssertionError("untrusted data reached deserialization"))
    monkeypatch.setattr(joblib, "load", loader)
    with pytest.raises(ModelUnavailable) as raised:
        _load_verified_bundle(directory, expected)
    assert raised.value.category == FailureCategory.ARTIFACT_INVALID
    loader.assert_not_called()


@pytest.mark.parametrize("field,value,category", [
    ("library_versions", {}, FailureCategory.RUNTIME_INCOMPATIBLE),
    ("features", {"input_column": "conversation"}, FailureCategory.ARTIFACT_INVALID),
    ("class_mapping", {"0": "UNKNOWN"}, FailureCategory.ARTIFACT_INVALID),
    ("unsupported_labels", [], FailureCategory.ARTIFACT_INVALID),
    ("requires_human_review", False, FailureCategory.ARTIFACT_INVALID),
    ("automated_actions_allowed", True, FailureCategory.ARTIFACT_INVALID),
    ("model_version", "unapproved", FailureCategory.ARTIFACT_INVALID),
])
def test_reviewed_but_incompatible_metadata_is_rejected(bundle, field, value, category):
    change_metadata(bundle, lambda metadata: metadata.__setitem__(field, value))
    with pytest.raises(ModelUnavailable) as raised:
        _load_verified_bundle(*bundle)
    assert raised.value.category == category


def test_authenticated_bytes_are_deserialized_from_memory(bundle, monkeypatch):
    import joblib
    original, seen = joblib.load, []
    def observe(source, **kwargs):
        seen.append(source)
        return original(source, **kwargs)
    monkeypatch.setattr(joblib, "load", observe)
    _load_verified_bundle(*bundle)
    assert len(seen) == 1 and hasattr(seen[0], "getvalue")
    assert hashlib.sha256(seen[0].getvalue()).hexdigest() == TRUSTED_SHA256["model.joblib"]


@pytest.mark.parametrize("defect", ["preprocessing", "taxonomy", "dimensions"])
def test_reviewed_pipeline_components_must_match_the_metadata(bundle, defect):
    import joblib
    directory, expected = bundle
    model = joblib.load(directory/"model.joblib")  # Copy of authenticated local fixture, never the protected original.
    if defect == "preprocessing":
        model.named_steps["tfidf"].input = "filename"
    elif defect == "taxonomy":
        model.named_steps["classifier"].classes_[0] = "UNKNOWN"
    else:
        model.named_steps["classifier"].coef_ = model.named_steps["classifier"].coef_[:, :-1]
    joblib.dump(model, directory/"model.joblib")
    expected["model.joblib"] = hashlib.sha256((directory/"model.joblib").read_bytes()).hexdigest()
    change_metadata(bundle, lambda metadata: metadata.__setitem__("artifact_sha256", expected["model.joblib"]))
    completed = json.loads((directory/"completed_run.json").read_text())
    completed["model_files"]["model.joblib"] = expected["model.joblib"]
    (directory/"completed_run.json").write_text(json.dumps(completed))
    expected["completed_run.json"] = hashlib.sha256((directory/"completed_run.json").read_bytes()).hexdigest()
    with pytest.raises(ModelUnavailable):
        _load_verified_bundle(directory, expected)


def test_missing_ml_dependency_degrades_safely(bundle, monkeypatch):
    import builtins
    original = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "joblib":
            raise ImportError("synthetic missing ML dependency")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", missing)
    with pytest.raises(ModelUnavailable) as raised:
        _load_verified_bundle(*bundle)
    assert raised.value.category == FailureCategory.RUNTIME_INCOMPATIBLE


def test_model_cache_is_shared_across_concurrent_requests():
    loader = Mock(return_value=Mock(predict=Mock(return_value=prediction())))
    service = InferenceService(loader=loader)
    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(lambda _: service.classify("Synthetic", "Synthetic description"), range(8)))
    loader.assert_called_once()
    assert all(outcome.status == ClassificationState.SUCCEEDED for outcome in outcomes)


def test_negative_cache_expires_and_allows_recovery():
    clock = [0.0]
    loader = Mock(side_effect=[ModelUnavailable(FailureCategory.MODEL_MISSING), Mock(predict=lambda text: prediction())])
    service = InferenceService(loader=loader, clock=lambda: clock[0])
    assert service.classify("Synthetic", "Synthetic").status == ClassificationState.UNAVAILABLE
    assert service.classify("Synthetic", "Synthetic").status == ClassificationState.UNAVAILABLE
    assert loader.call_count == 1
    clock[0] = 31
    assert service.classify("Synthetic", "Synthetic").status == ClassificationState.SUCCEEDED
    assert loader.call_count == 2


@pytest.mark.parametrize("subject,description", [(None, "Synthetic"), ([], "Synthetic"), ("", "Synthetic"),
    ("Synthetic", " \n "), ("x"*201, "Synthetic"), ("Synthetic", "x"*20001), ("Synthetic", "x\0")])
def test_malformed_input_fails_without_loading_model(subject, description):
    loader = Mock()
    outcome = InferenceService(loader=loader).classify(subject, description)
    assert outcome.status == ClassificationState.FAILED and outcome.failure_category == FailureCategory.INVALID_INPUT
    assert outcome.requires_human_review and outcome.predicted_intent is None
    loader.assert_not_called()


def test_long_ticket_is_explicitly_shortened_without_changing_original():
    original = "Synthetic description "*900
    model = Mock(predict=Mock(return_value=prediction()))
    outcome = InferenceService(loader=lambda: model).classify("Synthetic subject", original)
    passed = model.predict.call_args.args[0]
    assert len(passed) == 10000 and passed.startswith("Synthetic subject\n\nSynthetic description")
    assert outcome.input_truncated and outcome.input_characters == 10000
    assert original == "Synthetic description "*900


def test_disabled_model_and_prediction_failure_are_distinct():
    loader = Mock()
    disabled = InferenceService(enabled=False, loader=loader).classify("Synthetic", "Synthetic")
    assert disabled.status == ClassificationState.UNAVAILABLE and disabled.failure_category == FailureCategory.MODEL_DISABLED
    loader.assert_not_called()
    failed = InferenceService(loader=lambda: Mock(predict=Mock(side_effect=RuntimeError("DO_NOT_EXPOSE_TEXT")))).classify("Synthetic", "Synthetic")
    assert failed.status == ClassificationState.FAILED and failed.failure_category == FailureCategory.INFERENCE_FAILED
    assert "DO_NOT_EXPOSE_TEXT" not in failed.model_dump_json()


def test_unsupported_output_never_becomes_a_reliable_prediction():
    outcome = InferenceService(loader=lambda: Mock(predict=lambda text: prediction("OTHER_SUPPORT"))).classify("Synthetic", "Synthetic")
    assert outcome.status == ClassificationState.FAILED and outcome.failure_category == FailureCategory.UNSUPPORTED_OUTPUT
    assert outcome.predicted_intent is None and outcome.confidence is None


@pytest.mark.parametrize("field,value", [("confidence", float("nan")), ("confidence", 2),
    ("requires_human_review", False), ("automated_actions_allowed", True), ("artifact_sha256", "unapproved"),
    ("score_type", "CALIBRATED"), ("class_scores", {})])
def test_invalid_prediction_safety_or_probability_fails_closed(field, value):
    value_map = prediction() | {field: value}
    outcome = InferenceService(loader=lambda: Mock(predict=lambda text: value_map)).classify("Synthetic", "Synthetic")
    assert outcome.status == ClassificationState.FAILED and outcome.requires_human_review
    assert not outcome.automated_actions_allowed and outcome.confidence is None
