"""Small validation-only selection, then one final development fit and test pass."""
import importlib.metadata
import io
import platform
from pathlib import Path

import joblib
import numpy as np
from sklearn.dummy import DummyClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .config import CORE_LABELS, DATA_VERSION, MODEL_VERSION, SEED, SPLIT_VERSION
from .data import model_inputs
from .evaluate import error_summary, markdown_report, metrics
from .io import IntegrityError, digest_json, read_json, save_json_once, save_once, sha256
from .split import assert_no_leakage

CANDIDATE_C = (1.0, 4.0)


def library_versions() -> dict:
    packages = ("supportlens", "pandas", "numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl")
    return {"python": platform.python_version(), **{name: importlib.metadata.version(name) for name in packages}}


def feature_configuration() -> dict:
    return {"input_column": "initial_customer_message", "analyzer": "word", "ngram_range": [1, 2], "min_df": 1, "sublinear_tf": True, "strip_accents": "unicode"}


def make_pipeline(c_value: float, seed: int = SEED) -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=1, sublinear_tf=True, strip_accents="unicode")),
        ("classifier", LogisticRegression(C=c_value, class_weight="balanced", solver="lbfgs", max_iter=2000, random_state=seed)),
    ])


def fit_candidates(frame, seed: int = SEED):
    train = frame.loc[frame["training_eligible"] & frame["split"].eq("train")]
    validation = frame.loc[frame["training_eligible"] & frame["split"].eq("validation")]
    candidates = []
    fitted = []
    for c_value in CANDIDATE_C:
        model = make_pipeline(c_value, seed)
        model.fit(model_inputs(train), train["intent"].tolist())
        scores = metrics(validation["intent"].tolist(), model.predict(model_inputs(validation)).tolist())
        candidates.append({"C": c_value, "validation_macro_f1": scores["macro_f1"], "validation_metrics": scores})
        fitted.append(model)
    # Ordered tie break prefers stronger regularization. Test is never referenced here.
    selected_index = max(range(len(candidates)), key=lambda i: (candidates[i]["validation_macro_f1"], -candidates[i]["C"]))
    return candidates, fitted[selected_index], candidates[selected_index]["C"]


def training_metadata(provenance: dict, c_value: float, artifact_hash: str, classes: list[str], seed: int = SEED) -> dict:
    return {
        "schema_version": 1, "model_version": MODEL_VERSION, "data_version": DATA_VERSION,
        "split_version": SPLIT_VERSION, "seed": seed, "provenance": provenance,
        "features": feature_configuration(), "library_versions": library_versions(),
        "model_parameters": {"C": c_value, "class_weight": "balanced", "solver": "lbfgs", "max_iter": 2000, "random_state": seed},
        "class_mapping": {str(i): label for i, label in enumerate(classes)},
        "artifact_sha256": artifact_hash, "artifact_file": "model.joblib",
        "confidence_policy": "UNCALIBRATED_RAW_PROBABILITY_ALWAYS_REVIEW",
        "requires_human_review": True, "automated_actions_allowed": False,
        "unsupported_labels": ["UNKNOWN", "NON_SUPPORT", "OTHER_SUPPORT"],
        "trusted_local_artifact_only": True,
    }


def train_and_evaluate(frame, split_manifest: dict, model_dir: Path, evaluation_dir: Path, provenance: dict) -> dict:
    assert_no_leakage(frame)
    fingerprint = digest_json({"provenance": provenance, "versions": library_versions(), "candidates": CANDIDATE_C, "seed": SEED})
    marker = model_dir / "completed_run.json"
    if marker.exists():
        existing = read_json(marker)
        if existing.get("run_fingerprint") != fingerprint:
            raise IntegrityError("Established training run differs; a reviewed new model version is required.")
        for relative, expected in existing["model_files"].items():
            if sha256(model_dir / relative) != expected:
                raise IntegrityError("Existing model artifact changed.")
        for relative, expected in existing["evaluation_files"].items():
            if sha256(evaluation_dir / relative) != expected:
                raise IntegrityError("Existing evaluation artifact changed.")
        return read_json(evaluation_dir / "results.json")
    if model_dir.exists() or evaluation_dir.exists():
        raise IntegrityError("Incomplete run directory exists; inspect it and choose a new explicit version.")
    # Atomically claim the version before accessing any test examples. An interrupted
    # run remains visibly incomplete and cannot be silently retried under this version.
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        model_dir.mkdir()
    except FileExistsError as exc:
        raise IntegrityError("Another process already claimed this model version.") from exc
    train = frame.loc[frame["training_eligible"] & frame["split"].eq("train")]
    val = frame.loc[frame["training_eligible"] & frame["split"].eq("validation")]
    test = frame.loc[frame["training_eligible"] & frame["split"].eq("test")]
    dev = frame.loc[frame["training_eligible"] & frame["split"].isin(("train", "validation"))]
    candidates, validation_model, selected_c = fit_candidates(frame)
    majority_validation = DummyClassifier(strategy="most_frequent").fit(np.zeros((len(train), 1)), train["intent"])
    validation_metrics = {
        "majority": metrics(val["intent"].tolist(), majority_validation.predict(np.zeros((len(val), 1))).tolist()),
        "tfidf_logistic_regression": metrics(val["intent"].tolist(), validation_model.predict(model_inputs(val)).tolist()),
    }
    model = make_pipeline(selected_c)
    model.fit(model_inputs(dev), dev["intent"].tolist())
    majority = DummyClassifier(strategy="most_frequent").fit(np.zeros((len(dev), 1)), dev["intent"])
    # The held-out primary test set is predicted exactly once, after C selection and development fitting.
    test_messages = model_inputs(test)
    test_scores = model.predict_proba(test_messages)
    test_predictions = model.classes_[test_scores.argmax(axis=1)].tolist()
    test_metrics = {
        "majority": metrics(test["intent"].tolist(), majority.predict(np.zeros((len(test), 1))).tolist()),
        "tfidf_logistic_regression": metrics(test["intent"].tolist(), test_predictions),
    }
    heldout_diagnostics = frame.loc[~frame["training_eligible"] & frame["split"].eq("test")]
    diag_core = heldout_diagnostics.loc[heldout_diagnostics["intent"].isin(CORE_LABELS)]
    diag_scores = metrics(diag_core["intent"].tolist(), model.predict(model_inputs(diag_core)).tolist()) if len(diag_core) else metrics([], [])
    outside = heldout_diagnostics.loc[~heldout_diagnostics["intent"].isin(CORE_LABELS)]
    outside_choices = model.predict(model_inputs(outside)).tolist() if len(outside) else []
    # Always report every slice count, including slices that cannot support metrics.
    diagnostics = {
        "counts_by_partition": {name: {
            "all_rows": len(part), "primary_eligible": int(part["training_eligible"].sum()),
            "needs_context": int(part["needs_context"].sum()), "language_issue": int(part["language_issue"].sum()),
            "outside_core_labels": int((~part["intent"].isin(CORE_LABELS)).sum()),
        } for name, part in frame.groupby("split")},
        "heldout_flagged_core": diag_scores,
        "heldout_outside_core": {"samples": len(outside), "forced_core_prediction_counts": {label: outside_choices.count(label) for label in CORE_LABELS}, "accuracy": None, "reason": "Unsupported labels are never recognized by this seven-class classifier."},
        "policy": "Only test-group diagnostic scores are reported; no metrics on quarantine or training-related diagnostic examples.",
    }
    result = {
        "schema_version": 1, "model_version": MODEL_VERSION, "run_fingerprint": fingerprint,
        "procedure": "Fit two fixed C candidates (1 and 4) on primary train only; select validation macro-F1, ties prefer C=1. Refit selected pipeline and majority baseline on primary train+validation, then evaluate primary test once. No test-driven tuning; established runs are returned without reevaluation.",
        "selected_C": selected_c, "selection": candidates,
        "validation": validation_metrics, "test": test_metrics,
        "development_samples": len(dev), "diagnostics": diagnostics,
        "split_summary": split_manifest["metadata"],
        "class_imbalance": {"all_gold": frame["intent"].value_counts().sort_index().to_dict(), "primary_eligible": frame.loc[frame["training_eligible"], "intent"].value_counts().sort_index().to_dict()},
        "exclusion_counts_by_class": {label: {reason: int(part["exclusion_reasons"].str.contains(reason, regex=False).sum()) for reason in ("OUTSIDE_CORE_LABELS", "NEEDS_CONTEXT", "LANGUAGE_ISSUE", "DAMAGED_UNRESOLVED", "INCOMPLETE_ANCESTRY_COMPONENT")} for label, part in frame.groupby("intent")},
        "test_error_analysis": error_summary(test["intent"].tolist(), test_predictions, test_scores, test_messages),
        "preliminary": True, "human_review_required": True,
        "privacy": "Aggregate metrics only. No customer text or record identifiers.",
    }
    # The public report must not inherit private record membership from the manifest.
    artifact_bytes = io.BytesIO()
    joblib.dump(model, artifact_bytes, compress=3)
    import hashlib
    model_hash = hashlib.sha256(artifact_bytes.getvalue()).hexdigest()
    metadata = training_metadata(provenance, selected_c, model_hash, model.classes_.tolist())
    save_once(model_dir / "model.joblib", artifact_bytes.getvalue())
    save_json_once(model_dir / "metadata.json", metadata)
    save_json_once(evaluation_dir / "results.json", result)
    save_once(evaluation_dir / "report.md", markdown_report(result))
    save_json_once(model_dir / "completed_run.json", {
        "run_fingerprint": fingerprint,
        "model_files": {name: sha256(model_dir / name) for name in ("model.joblib", "metadata.json")},
        "evaluation_files": {name: sha256(evaluation_dir / name) for name in ("results.json", "report.md")},
    })
    return result
