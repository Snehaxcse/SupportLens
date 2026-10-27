"""Trusted-local artifact inference with an unconditional human-review policy."""
from pathlib import Path
import re

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

from .config import CORE_LABELS, DATA_VERSION, MAX_INPUT_CHARACTERS, MODEL_VERSION, SPLIT_VERSION
from .io import IntegrityError, read_json, sha256
from .train import library_versions


class ModelArtifactError(IntegrityError):
    """A local artifact is missing, corrupt, incompatible or not an M1 model."""


def validate_message(message: object) -> str:
    if not isinstance(message, str):
        raise ValueError("Customer message must be a string.")
    if not message.strip():
        raise ValueError("Customer message must not be blank.")
    if len(message) > MAX_INPUT_CHARACTERS:
        raise ValueError("Customer message exceeds the 10000-character limit.")
    return message.strip()


class IntentClassifier:
    """Only load artifacts created locally by this project, never downloaded pickles.

    SHA-256 detects corruption; it does not authenticate an attacker-controlled
    model and sidecar. joblib is executable serialization, not a sandbox.
    """

    def __init__(self, model_directory: str | Path):
        directory = Path(model_directory)
        try:
            metadata = read_json(directory / "metadata.json")
            if (metadata.get("schema_version") != 1
                    or metadata.get("model_version") != MODEL_VERSION
                    or metadata.get("data_version") != DATA_VERSION
                    or metadata.get("split_version") != SPLIT_VERSION
                    or metadata.get("features", {}).get("input_column") != "initial_customer_message"
                    or metadata.get("confidence_policy") != "UNCALIBRATED_RAW_PROBABILITY_ALWAYS_REVIEW"
                    or metadata.get("unsupported_labels") != ["UNKNOWN", "NON_SUPPORT", "OTHER_SUPPORT"]
                    or not re.fullmatch(r"[0-9a-f]{64}", metadata.get("artifact_sha256", ""))):
                raise ModelArtifactError("Model metadata schema is invalid.")
            artifact = directory / "model.joblib"
            if artifact.stat().st_size > 20_000_000 or sha256(artifact) != metadata["artifact_sha256"]:
                raise ModelArtifactError("Model integrity check failed.")
            if metadata["library_versions"] != library_versions():
                raise ModelArtifactError("Model runtime versions do not match.")
            if metadata.get("requires_human_review") is not True or metadata.get("automated_actions_allowed") is not False:
                raise ModelArtifactError("Model safety contract is invalid.")
            model = joblib.load(artifact)
            if not isinstance(model, Pipeline) or list(model.named_steps) != ["tfidf", "classifier"]:
                raise ModelArtifactError("Unexpected classifier artifact type.")
            if not isinstance(model.named_steps["tfidf"], TfidfVectorizer) or not isinstance(model.named_steps["classifier"], LogisticRegression):
                raise ModelArtifactError("Unexpected pipeline components.")
            if set(model.classes_) != set(CORE_LABELS):
                raise ModelArtifactError("Unexpected model class mapping.")
            if metadata["class_mapping"] != {str(i): label for i, label in enumerate(model.classes_)}:
                raise ModelArtifactError("Model class metadata does not match.")
            self._model, self._metadata = model, metadata
        except ModelArtifactError:
            raise
        except Exception as exc:
            raise ModelArtifactError("Model is missing, corrupt or incompatible; inference is unavailable.") from exc

    def predict(self, message: str) -> dict:
        text = validate_message(message)
        scores = self._model.predict_proba([text])[0]
        if not np.isfinite(scores).all() or (scores < 0).any() or not np.isclose(scores.sum(), 1):
            raise ModelArtifactError("Classifier returned invalid scores.")
        index = int(np.argmax(scores))
        return {
            "predicted_intent": str(self._model.classes_[index]),
            "confidence": float(scores[index]), "score_type": "UNCALIBRATED_RAW_PROBABILITY",
            "class_scores": {str(label): float(value) for label, value in zip(self._model.classes_, scores)},
            "requires_human_review": True, "abstained_from_automated_handling": True,
            "review_reason": "UNVALIDATED_CONFIDENCE", "automated_actions_allowed": False,
            "model_version": self._metadata["model_version"],
            "artifact_sha256": self._metadata["artifact_sha256"],
            "data_version": self._metadata["data_version"], "split_version": self._metadata["split_version"],
            "unsupported_labels": list(self._metadata["unsupported_labels"]),
        }
