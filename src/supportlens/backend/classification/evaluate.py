"""Reproducible synthetic integration checks; no training, held-out data or quality metrics."""
import argparse
import json
import math

from .constants import CORE_LABELS, MODEL_VERSION, TRUSTED_SHA256
from .inference import InferenceService, compose_ticket, load_trusted_classifier
from .schemas import ClassificationState

CASES = (
    ("SYNTHETIC_LOGIN", "Synthetic sign-in question", "A synthetic account cannot sign in after a password reset."),
    ("SYNTHETIC_SECURITY", "Synthetic account security", "A synthetic test account reports an unfamiliar login."),
    ("SYNTHETIC_BILLING", "Synthetic payment question", "A synthetic subscription payment appears twice in a test statement."),
    ("SYNTHETIC_PLAYBACK", "Synthetic playback issue", "Synthetic music stops on a test device during playback."),
    ("SYNTHETIC_FEATURE", "Synthetic feature request", "A synthetic user requests a new playlist sorting option."),
    ("SYNTHETIC_LONG", "Synthetic long ticket", "Synthetic reproduction details. "*500),
)


def evaluate(threshold=0.5):
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("An advisory threshold between zero and one is required.")
    reference = load_trusted_classifier()
    service = InferenceService(loader=lambda: reference)
    cases = []
    for name, subject, description in CASES:
        first, repeated = service.classify(subject, description), service.classify(subject, description)
        text, truncated = compose_ticket(subject, description)
        baseline = reference.predict(text)
        assert first.status == repeated.status == ClassificationState.SUCCEEDED
        assert first.predicted_intent in CORE_LABELS and first.confidence == baseline["confidence"]
        assert first.predicted_intent == baseline["predicted_intent"] == repeated.predicted_intent
        assert first.confidence == repeated.confidence and first.class_scores == repeated.class_scores
        assert first.requires_human_review and not first.automated_actions_allowed
        cases.append({"case": name, "status": first.status.value, "predicted_intent": first.predicted_intent.value,
            "uncalibrated_model_score": first.confidence, "low_score_advisory_flag": first.confidence < threshold,
            "input_characters": len(text), "input_truncated": truncated, "m1_prediction_parity": True, "deterministic": True})
    invalid = service.classify("", "Synthetic invalid input")
    unavailable = InferenceService(enabled=False).classify("Synthetic disabled model", "Synthetic description")
    assert invalid.status == ClassificationState.FAILED and unavailable.status == ClassificationState.UNAVAILABLE
    return {"schema_version": 1, "model_version": MODEL_VERSION, "artifact_sha256": TRUSTED_SHA256["model.joblib"],
        "status": "PASS", "synthetic_cases": cases, "advisory_threshold": threshold, "threshold_validated": False,
        "invalid_input_rejected": True, "simulated_disabled_model_distinguished": True,
        "requires_human_review": True, "automated_actions_allowed": False,
        "predictive_quality_measured": False, "heldout_reevaluated": False, "model_retrained": False,
        "scope": "Synthetic invocation/mapping/input-boundary correctness. Persistence and UI are verified by separate tests and runtime checks."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(evaluate(args.threshold), indent=2))
        return 0
    except Exception:
        print(json.dumps({"status": "UNAVAILABLE", "details_suppressed": True}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
