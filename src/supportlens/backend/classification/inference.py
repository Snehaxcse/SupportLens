"""Lazy, pinned-byte model loading; all computation happens outside database transactions."""
import hashlib
import io
import json
import math
import threading
import time

from supportlens.ml.config import MAX_INPUT_CHARACTERS
from .constants import CORE_LABELS, DATA_VERSION, MODEL_DIRECTORY, MODEL_VERSION, SCORE_TYPE, SPLIT_VERSION, TRUSTED_SHA256, UNSUPPORTED
from .schemas import ClassificationState, FailureCategory, InferenceResult


class ModelUnavailable(RuntimeError):
    def __init__(self, category):
        self.category = category
        super().__init__("The reviewed classifier is unavailable.")


def _load_verified_bundle(directory, expected):
    """Internal testing seam; production always supplies the fixed, reviewed constants."""
    contents = {}
    try:
        for name in ("model.joblib", "metadata.json", "completed_run.json"):
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID)
            limit = 20_000_000 if name == "model.joblib" else 100_000
            with path.open("rb") as handle:
                data = handle.read(limit + 1)
            if len(data) > limit or hashlib.sha256(data).hexdigest() != expected[name]:
                raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID)
            contents[name] = data
        metadata = json.loads(contents["metadata.json"])
        completed = json.loads(contents["completed_run.json"])
        if (metadata.get("schema_version") != 1 or metadata.get("model_version") != MODEL_VERSION
                or metadata.get("data_version") != DATA_VERSION or metadata.get("split_version") != SPLIT_VERSION
                or metadata.get("confidence_policy") != "UNCALIBRATED_RAW_PROBABILITY_ALWAYS_REVIEW"
                or metadata.get("requires_human_review") is not True or metadata.get("automated_actions_allowed") is not False
                or metadata.get("trusted_local_artifact_only") is not True
                or metadata.get("unsupported_labels") != list(UNSUPPORTED)
                or metadata.get("artifact_sha256") != expected["model.joblib"]
                or completed.get("model_files") != {name: expected[name] for name in ("model.joblib", "metadata.json")}):
            raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID)
    except FileNotFoundError:
        raise ModelUnavailable(FailureCategory.MODEL_MISSING) from None
    except ModelUnavailable:
        raise
    except Exception:
        raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID) from None
    try:
        # Missing/incompatible ML dependencies must not stop API process health or ticket creation.
        import joblib
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import Pipeline
        from supportlens.ml.inference import IntentClassifier
        from supportlens.ml.train import feature_configuration, library_versions
        if metadata["library_versions"] != library_versions():
            raise ModelUnavailable(FailureCategory.RUNTIME_INCOMPATIBLE)
        if metadata.get("features") != feature_configuration():
            raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID)
        # Deserialize exactly the bytes just authenticated; never reopen an unchecked model path.
        model = joblib.load(io.BytesIO(contents["model.joblib"]))
        if type(model) is not Pipeline or list(model.named_steps) != ["tfidf", "classifier"]:
            raise ValueError
        vectorizer, estimator = model.named_steps["tfidf"], model.named_steps["classifier"]
        if type(vectorizer) is not TfidfVectorizer or type(estimator) is not LogisticRegression:
            raise ValueError
        expected_classes = sorted(CORE_LABELS)
        if (list(model.classes_) != expected_classes
                or metadata["class_mapping"] != {str(i): label for i, label in enumerate(expected_classes)}):
            raise ValueError
        parameters = vectorizer.get_params()
        features = {"input": "content", "analyzer": "word", "ngram_range": (1, 2), "min_df": 1,
                    "sublinear_tf": True, "strip_accents": "unicode", "preprocessor": None, "tokenizer": None}
        if any(parameters[key] != value for key, value in features.items()):
            raise ValueError
        if any(estimator.get_params()[key] != value for key, value in metadata["model_parameters"].items()):
            raise ValueError
        width = len(vectorizer.vocabulary_)
        if (estimator.coef_.shape != (len(CORE_LABELS), width) or estimator.n_features_in_ != width
                or len(vectorizer.idf_) != width or not np.isfinite(estimator.coef_).all()
                or not np.isfinite(vectorizer.idf_).all()):
            raise ValueError

        class VerifiedClassifier(IntentClassifier):
            def __init__(self):
                self._model, self._metadata = model, metadata
            # The original M1 prediction, preprocessing and safety policy are inherited unchanged.

        return VerifiedClassifier()
    except ModelUnavailable:
        raise
    except ImportError:
        raise ModelUnavailable(FailureCategory.RUNTIME_INCOMPATIBLE) from None
    except Exception:
        raise ModelUnavailable(FailureCategory.ARTIFACT_INVALID) from None


def load_trusted_classifier():
    return _load_verified_bundle(MODEL_DIRECTORY, TRUSTED_SHA256)


def compose_ticket(subject, description):
    for value, maximum in ((subject, 200), (description, 20000)):
        if (not isinstance(value, str) or not value.strip() or len(value) > maximum
                or any(ord(c) < 32 and c not in "\n\r\t" for c in value)):
            raise ValueError("Invalid ticket classification input.")
    text = subject.strip() + "\n\n" + description.strip()
    return text[:MAX_INPUT_CHARACTERS], len(text) > MAX_INPUT_CHARACTERS


class InferenceService:
    def __init__(self, *, enabled=True, loader=load_trusted_classifier, clock=time.monotonic, negative_cache_seconds=30):
        self.enabled, self.loader, self.clock = enabled, loader, clock
        self.negative_cache_seconds = negative_cache_seconds
        self._model = None
        self._failure = None
        self._retry_at = 0
        self._lock = threading.Lock()

    def classify(self, subject, description):
        started = self.clock()
        text, truncated, verified = "", False, False
        def result(status, **fields):
            return InferenceResult(status=status, duration_ms=round(max(0, self.clock()-started)*1000, 3),
                input_characters=len(text), input_truncated=truncated, artifact_verified=verified, **fields)
        try:
            text, truncated = compose_ticket(subject, description)
        except (ValueError, TypeError):
            return result(ClassificationState.FAILED, failure_category=FailureCategory.INVALID_INPUT)
        if not self.enabled:
            return result(ClassificationState.UNAVAILABLE, failure_category=FailureCategory.MODEL_DISABLED)
        try:
            with self._lock:
                if self._model is None:
                    if self._failure is not None and self.clock() < self._retry_at:
                        raise ModelUnavailable(self._failure)
                    try:
                        self._model = self.loader()
                        self._failure = None
                    except ModelUnavailable as error:
                        self._failure, self._retry_at = error.category, self.clock()+self.negative_cache_seconds
                        raise
                verified = True
                prediction = self._model.predict(text)
            label = prediction.get("predicted_intent")
            if label not in CORE_LABELS:
                return result(ClassificationState.FAILED, failure_category=FailureCategory.UNSUPPORTED_OUTPUT)
            confidence, scores = prediction.get("confidence"), prediction.get("class_scores", {})
            if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1
                    or set(scores) != set(CORE_LABELS)
                    or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or not 0 <= v <= 1 for v in scores.values())
                    or not math.isclose(sum(scores.values()), 1, abs_tol=1e-6)
                    or not math.isclose(confidence, max(scores.values()), abs_tol=1e-9)
                    or not math.isclose(scores[label], confidence, abs_tol=1e-9)
                    or prediction.get("model_version") != MODEL_VERSION
                    or prediction.get("artifact_sha256") != TRUSTED_SHA256["model.joblib"]
                    or prediction.get("score_type") != SCORE_TYPE
                    or prediction.get("requires_human_review") is not True
                    or prediction.get("automated_actions_allowed") is not False):
                raise ValueError
            return result(ClassificationState.SUCCEEDED, predicted_intent=label, confidence=confidence, class_scores=scores)
        except ModelUnavailable as error:
            return result(ClassificationState.UNAVAILABLE, failure_category=error.category)
        except Exception:
            return result(ClassificationState.FAILED, failure_category=FailureCategory.INFERENCE_FAILED)
