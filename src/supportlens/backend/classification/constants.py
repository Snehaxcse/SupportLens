"""Reviewed artifact identity. Neither HTTP input nor environment can select a model."""
from pathlib import Path

from supportlens.ml.config import CORE_LABELS, DATA_VERSION, MODEL_VERSION, SPLIT_VERSION

KIND = "supportlens.intent-classification.v1"
MODEL_DIRECTORY = Path(__file__).resolve().parents[4] / "artifacts" / "models" / MODEL_VERSION
TRUSTED_SHA256 = {
    "model.joblib": "6c5d3088aa6222658d4ce5a5d4753c2496cebdf11f8401aa3e959fe29bd63e4f",
    "metadata.json": "db41bfdd874604fd33e19f929a12c6b35a5a0db987b8ba47d2e3d4bd57a48db4",
    "completed_run.json": "0aaec677d76e871c51071b947e05da992fe313d4015213add25c67fb012a0dff",
}
UNSUPPORTED = ("UNKNOWN", "NON_SUPPORT", "OTHER_SUPPORT")
SCORE_TYPE = "UNCALIBRATED_RAW_PROBABILITY"
INPUT_SCHEME = "SUBJECT_BLANKLINE_DESCRIPTION_V1"
MODEL_LIMITATION = (
    "This seven-class model cannot detect UNKNOWN, NON_SUPPORT or OTHER_SUPPORT; "
    "it still chooses a trained intent. APP_TECHNICAL recall was 0/4 in the small M1 held-out evaluation."
)
