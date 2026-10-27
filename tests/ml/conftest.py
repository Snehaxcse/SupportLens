from pathlib import Path

import joblib
import pandas as pd
import pytest

from supportlens.ml.config import CORE_LABELS
from supportlens.ml.io import save_json_once, sha256
from supportlens.ml.train import make_pipeline, training_metadata


def annotation(tid="1", text="Synthetic customer cannot access the demo account.", label="ACCOUNT_ACCESS", **changes):
    row = {
        "thread_id": tid, "initial_customer_message": text, "customer_context": text,
        "conversation": "CUSTOMER: " + text + "\nSPOTIFY: Synthetic agent response.",
        "discovery_label": "", "intent": label, "needs_context": "FALSE",
        "language_issue": "false", "annotation_notes": "", "annotation_source": "EARLIER_GOLD",
    }
    row.update(changes)
    return row


@pytest.fixture
def gold_factory():
    return lambda *records: pd.DataFrame(records or [annotation()])


@pytest.fixture
def split_frame():
    rows = []
    for label_index, label in enumerate(CORE_LABELS):
        for number in range(25):
            tid = str(1 + label_index * 25 + number)
            row = annotation(tid, f"Synthetic {label} signal unique_{tid}.", label)
            row.update({"needs_context": False, "language_issue": False, "core_intent": True,
                        "group_id": "group_" + tid, "ancestry_status": "complete", "group_quarantined": False,
                        "corpus_component_size": 1, "correction_status": "unchanged",
                        "exclusion_reasons": "", "training_eligible": True})
            rows.append(row)
    return pd.DataFrame(rows)


@pytest.fixture
def assigned_frame(split_frame):
    frame = split_frame.copy()
    frame["split"] = ["train" if int(row.thread_id) % 25 < 17 else "validation" if int(row.thread_id) % 25 < 21 else "test" for row in frame.itertuples()]
    return frame


@pytest.fixture
def local_model(tmp_path):
    folder = tmp_path / "local_model"
    folder.mkdir()
    model = make_pipeline(1.0)
    model.fit(["Synthetic issue " + label for label in CORE_LABELS], list(CORE_LABELS))
    joblib.dump(model, folder / "model.joblib", compress=3)
    metadata = training_metadata({"synthetic": True}, 1.0, sha256(folder / "model.joblib"), model.classes_.tolist())
    save_json_once(folder / "metadata.json", metadata)
    return folder
