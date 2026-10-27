"""Aggregate-only metrics and privacy-preserving error summaries."""
import numpy as np
from sklearn.metrics import (
    accuracy_score, classification_report, confusion_matrix,
    f1_score, precision_score, recall_score,
)

from .config import CORE_LABELS


def metrics(y_true, y_pred) -> dict:
    labels = list(CORE_LABELS)
    if not len(y_true):
        return {"samples": 0, "status": "NOT_EVALUATED_EMPTY_SLICE"}
    report = classification_report(y_true, y_pred, labels=labels, output_dict=True, zero_division=0)
    return {
        "samples": len(y_true), "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_precision": float(precision_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "macro_recall": float(recall_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "macro_f1": float(f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0)),
        "per_class": {label: {key: float(value) if key != "support" else int(value) for key, value in report[label].items()} for label in labels},
        "confusion_matrix": {"labels": labels, "rows_true_columns_predicted": confusion_matrix(y_true, y_pred, labels=labels).tolist()},
        "preliminary": True,
        "limitation": "Small, selected annotation set; all scores are preliminary, not production accuracy estimates.",
    }


def error_summary(y_true, y_pred, raw_scores, messages) -> dict:
    mistakes = np.array(y_true) != np.array(y_pred)
    confidence = np.max(raw_scores, axis=1)
    buckets = {"under_10_words": [0, 0], "10_to_29_words": [0, 0], "30plus_words": [0, 0]}
    for text, wrong in zip(messages, mistakes):
        length = len(text.split())
        key = "under_10_words" if length < 10 else "10_to_29_words" if length < 30 else "30plus_words"
        buckets[key][0] += 1
        buckets[key][1] += int(wrong)
    return {
        "errors": int(mistakes.sum()),
        "length_buckets": {key: {"samples": values[0], "errors": values[1]} for key, values in buckets.items()},
        "mean_raw_probability_when_wrong": float(confidence[mistakes].mean()) if mistakes.any() else None,
        "mean_raw_probability_when_correct": float(confidence[~mistakes].mean()) if (~mistakes).any() else None,
        "privacy": "No messages, record IDs, tokens, vocabulary or example excerpts are exported.",
    }


def markdown_report(result: dict) -> str:
    lines = ["# SupportLens M1 evaluation", "", "All results are preliminary. This seven-class model always requires human review.", "",
             "## Procedure", "", result["procedure"], "", "## Primary results", "",
             "| Partition | Model | N | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for partition in ("validation", "test"):
        for name, report in result[partition].items():
            lines.append(f"| {partition} | {name} | {report['samples']} | {report['accuracy']:.4f} | {report['macro_precision']:.4f} | {report['macro_recall']:.4f} | {report['macro_f1']:.4f} | {report['weighted_f1']:.4f} |")
    for partition in ("validation", "test"):
        report = result[partition]["tfidf_logistic_regression"]
        lines += ["", f"## {partition.title()} per-class classifier results", "",
                  "| Intent | Precision | Recall | F1 | Support |", "|---|---:|---:|---:|---:|"]
        for label, values in report["per_class"].items():
            lines.append(f"| {label} | {values['precision']:.4f} | {values['recall']:.4f} | {values['f1-score']:.4f} | {values['support']} |")
        lines += ["", "Confusion matrix: rows are true labels; columns are predictions, in the order below.", "",
                  "`" + ", ".join(CORE_LABELS) + "`", "", "```text"]
        lines.extend(" ".join(str(v) for v in row) for row in report["confusion_matrix"]["rows_true_columns_predicted"])
        lines += ["```"]
    lines += ["", "## Diagnostics and limitations", "",
              "See results.json for slice counts, selection scores, class imbalance and aggregate error analysis.", "",
              "Flagged records are excluded from fitting. Only test-group diagnostics are held out from the final development fit. Quarantined ancestry components are never scored as held-out examples.", "",
              "UNKNOWN, OTHER_SUPPORT and NON_SUPPORT are not model classes. Predictions on those test-group records describe forced core-class choices, not successful out-of-scope detection.", "",
              "The dataset combines random/discovery and keyword-targeted annotation. Per-class test support is small; confidence is uncalibrated. Beating the majority reference does not establish safe automation or production readiness.", "",
              "No supervised accuracy is estimated on the 29,395 unlabeled threads. No customer text appears in this report.", ""]
    return "\n".join(lines)
