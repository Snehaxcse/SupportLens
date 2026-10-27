# SupportLens M1 evaluation

All results are preliminary. This seven-class model always requires human review.

## Procedure

Fit two fixed C candidates (1 and 4) on primary train only; select validation macro-F1, ties prefer C=1. Refit selected pipeline and majority baseline on primary train+validation, then evaluate primary test once. No test-driven tuning; established runs are returned without reevaluation.

## Primary results

| Partition | Model | N | Accuracy | Macro precision | Macro recall | Macro F1 | Weighted F1 |
|---|---|---:|---:|---:|---:|---:|---:|
| validation | majority | 34 | 0.2353 | 0.0336 | 0.1429 | 0.0544 | 0.0896 |
| validation | tfidf_logistic_regression | 34 | 0.5882 | 0.6279 | 0.6274 | 0.6127 | 0.5895 |
| test | majority | 35 | 0.2571 | 0.0367 | 0.1429 | 0.0584 | 0.1052 |
| test | tfidf_logistic_regression | 35 | 0.6571 | 0.6429 | 0.6667 | 0.6227 | 0.6313 |

## Validation per-class classifier results

| Intent | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| ACCOUNT_ACCESS | 1.0000 | 0.8000 | 0.8889 | 5 |
| ACCOUNT_SECURITY | 1.0000 | 1.0000 | 1.0000 | 4 |
| BILLING_PAYMENT | 0.6667 | 0.4000 | 0.5000 | 5 |
| SUBSCRIPTION | 0.5000 | 0.7500 | 0.6000 | 4 |
| APP_TECHNICAL | 0.4000 | 0.6667 | 0.5000 | 3 |
| CONTENT | 0.4000 | 0.4000 | 0.4000 | 5 |
| FEATURE | 0.4286 | 0.3750 | 0.4000 | 8 |

Confusion matrix: rows are true labels; columns are predictions, in the order below.

`ACCOUNT_ACCESS, ACCOUNT_SECURITY, BILLING_PAYMENT, SUBSCRIPTION, APP_TECHNICAL, CONTENT, FEATURE`

```text
4 0 0 0 1 0 0
0 4 0 0 0 0 0
0 0 2 2 0 0 1
0 0 0 3 0 1 0
0 0 1 0 2 0 0
0 0 0 0 0 2 3
0 0 0 1 2 2 3
```

## Test per-class classifier results

| Intent | Precision | Recall | F1 | Support |
|---|---:|---:|---:|---:|
| ACCOUNT_ACCESS | 0.5000 | 1.0000 | 0.6667 | 3 |
| ACCOUNT_SECURITY | 0.6000 | 0.6000 | 0.6000 | 5 |
| BILLING_PAYMENT | 1.0000 | 0.4000 | 0.5714 | 5 |
| SUBSCRIPTION | 0.8000 | 1.0000 | 0.8889 | 4 |
| APP_TECHNICAL | 0.0000 | 0.0000 | 0.0000 | 4 |
| CONTENT | 1.0000 | 1.0000 | 1.0000 | 5 |
| FEATURE | 0.6000 | 0.6667 | 0.6316 | 9 |

Confusion matrix: rows are true labels; columns are predictions, in the order below.

`ACCOUNT_ACCESS, ACCOUNT_SECURITY, BILLING_PAYMENT, SUBSCRIPTION, APP_TECHNICAL, CONTENT, FEATURE`

```text
3 0 0 0 0 0 0
1 3 0 0 0 0 1
1 0 2 1 1 0 0
0 0 0 4 0 0 0
0 1 0 0 0 0 3
0 0 0 0 0 5 0
1 1 0 0 1 0 6
```

## Diagnostics and limitations

See results.json for slice counts, selection scores, class imbalance and aggregate error analysis.

Flagged records are excluded from fitting. Only test-group diagnostics are held out from the final development fit. Quarantined ancestry components are never scored as held-out examples.

UNKNOWN, OTHER_SUPPORT and NON_SUPPORT are not model classes. Predictions on those test-group records describe forced core-class choices, not successful out-of-scope detection.

The dataset combines random/discovery and keyword-targeted annotation. Per-class test support is small; confidence is uncalibrated. Beating the majority reference does not establish safe automation or production readiness.

No supervised accuracy is estimated on the 29,395 unlabeled threads. No customer text appears in this report.
