# SupportLens

SupportLens is a Spotify-focused support product in development. M1 implements a local intent-classification foundation. There is no backend, frontend, retrieval, Gemini integration, deployment, or real Spotify integration yet.

The model predicts seven core intents. Every prediction requires human review. It cannot authorize a reply, resolution, refund, account change, or subscription operation.

## Environment and installation (Windows PowerShell)

Use Python 3.11. The measured local runtime is 3.11.3, also recorded in `.python-version`. Node is not required for M1. Nothing is installed globally.

For a fresh checkout, create an environment without deleting any existing environment:

```powershell
py -3.11 -m venv .venv-m1
$SupportLensPython = ".\.venv-m1\Scripts\python.exe"
& $SupportLensPython -m pip --disable-pip-version-check install -r requirements-dev.txt
& $SupportLensPython -m pip --disable-pip-version-check check
```

The current local implementation uses `.venv`; substitute that directory when reproducing this run. `constraints.txt` pins runtime, transitive, test and build dependency versions. It is a version constraint set, not a package-hash supply-chain lock. The editable project uses the same pinned build backend. Python is restricted to 3.11 until other runtimes are tested.

## Local commands

Run from the repository root. Private datasets are intentionally not included in Git.

```powershell
$SupportLensPython = ".\.venv\Scripts\python.exe"
& $SupportLensPython -B -m pytest -p no:cacheprovider -q
& $SupportLensPython -B -m supportlens.ml.cli prepare
& $SupportLensPython -B -m supportlens.ml.cli train
& $SupportLensPython -B -m supportlens.ml.cli verify
& $SupportLensPython -B -m supportlens.ml.cli predict --message "Synthetic example: music playback stops when I lock the screen."
```

`prepare` validates and creates a derived dataset and fixed membership. Repeating it recomputes and compares established artifacts; a difference is an error. `train` returns the verified established run on repetition, without fitting or evaluating the test set again. An interrupted run remains incomplete and requires deliberate inspection and a reviewed new version. Never delete existing outputs merely to obtain better test scores.

If source data, split policy, or model code changes, introduce explicit reviewed data/split/model versions in `config.py`. Existing membership is never silently replaced. New experiments must not tune against the established test set. Current inference intentionally requires exactly matching recorded runtime/library versions.

## Source protection and validation

The approved canonical gold SHA-256 is:

```text
81d3ed3eb4a83ccc39cf25148a3e7a791038ad78eff0b0c9fd2b591874c9cc8a
```

Every dataset operation checks it before preparing data. Hash snapshots also protect raw data, every existing processed CSV, scripts 01–12, and `Decisions.md`. None of those files is rewritten. Do not rerun legacy preparation/merge scripts: several overwrite annotations, and script 08 writes back despite its analysis-oriented name.

Strict validation checks schema, string IDs, uniqueness, canonical labels, flags, provenance, blank text, source consistency, spreadsheet errors and duplicate relationships. Invalid rows fail explicitly. A damaged example is repaired only in the derived copy when the unique source ID and unchanged full conversation establish a match. Annotation fields are verified unchanged. Unresolvable damage receives an explicit exclusion. Private correction records contain IDs and source/hash references, never copied messages.

The original ten-label dataset remains intact. Supervised labels are ACCOUNT_ACCESS, ACCOUNT_SECURITY, BILLING_PAYMENT, SUBSCRIPTION, APP_TECHNICAL, CONTENT and FEATURE. UNKNOWN, NON_SUPPORT and OTHER_SUPPORT remain in analysis and grouping, but are not predicted or reliably detected.

Primary fitting and evaluation require both `needs_context` and `language_issue` false. Flagged records remain in the derived dataset and partition manifests. Exclusion reasons can overlap and must not be added as independent row totals.

## Leakage control and partition policy

Raw tweet-parent ancestry is reconstructed locally. Connected components combine:

- Shared ancestry, including shared known missing-parent IDs.
- NFKC/case/whitespace-normalized initial messages across the full 29,395-thread corpus, allowing unlabeled records to bridge gold groups.
- Conservatively detected near-duplicate pairs among gold, using a fixed 0.92 character-similarity threshold for texts at least 20 characters long.

Near-duplicate checking is lexical and limited to gold; it does not establish exhaustive semantic independence. A whole component is quarantined if any corpus member has incomplete ancestry or a cycle. Quarantine is neither fitted nor reported as held-out performance. Unobserved relationships cannot be ruled out.

A seeded search considers 4,096 group-level allocations, targeting 70/15/15. It uses label counts and group sizes only, never model scores. It requires at least 10 eligible training examples and 3 eligible validation/test examples per class, otherwise stops. These are feasibility gates for a preliminary experiment, not statistical evidence of adequate production support. All gold examples, including diagnostic and unsupported labels, inherit their component assignment.

Only `initial_customer_message` enters the model. Full conversation, later customer context, provenance, discovery labels, flags and outcomes are excluded. TF-IDF is inside the sklearn Pipeline and is fit on training messages only during selection.

## Training and evaluation procedure

Two fixed Logistic Regression candidates (`C=1` and `C=4`, balanced class weights) use word TF-IDF unigrams/bigrams, `min_df=1`, sublinear term frequency and a fixed seed. Select by validation macro-F1, breaking ties toward `C=1`. Refit the selected pipeline on eligible train+validation examples and predict the primary test partition once. Fit a majority-class reference on the same respective fitting partitions.

Evaluation JSON and Markdown distinguish validation from final test metrics and include accuracy, macro precision/recall/F1, weighted F1, per-class support, confusion matrices, imbalance and aggregate error analysis. Only test-group flagged diagnostics are treated as held out from the final fit. Unsupported-label examples receive forced-core-choice counts, not fabricated detection accuracy.

The dataset is small and combines discovery/random and keyword-targeted annotation. Metrics are preliminary and do not estimate production accuracy. No supervised accuracy is reported on the unlabeled corpus, and beating the majority reference does not validate automated handling. See `evaluation/m1_v1/report.md` for measured results.

## Python inference contract

```python
from supportlens.ml.inference import IntentClassifier

classifier = IntentClassifier("artifacts/models/m1_tfidf_lr_v1")
result = classifier.predict("Synthetic example: the demo music player keeps stopping.")
assert result["requires_human_review"] is True
assert result["automated_actions_allowed"] is False
```

The module validates message type, nonblank content and a 10,000-character maximum. It returns a predicted core label, raw class probabilities, model/data/split versions, artifact hash and an unconditional review recommendation. `confidence` is an **uncalibrated raw probability**, not a safety threshold. Missing, corrupt, incompatible or malformed artifacts fail closed. No customer input is echoed or stored by inference.

Load only artifacts created and retained locally by this project. Joblib uses executable serialization. SHA-256 detects corruption; an attacker who controls both binary and metadata can bypass an integrity checksum. Never load downloaded or otherwise untrusted pickle/joblib models.

## Artifacts and privacy

| Location | Contents | Git policy |
|---|---|---|
| `data/raw/`, `data/processed/` | Existing private source artifacts | Ignored; preserved |
| `data/derived/m1_v1/` | Derived text, validation/correction IDs, source hashes, grouping | Ignored |
| `data/splits/m1_split_v1/` | Stable record IDs, groups, assignments and generation metadata | Ignored |
| `artifacts/models/m1_tfidf_lr_v1/` | Classifier, metadata and completed-run checksums | Ignored |
| `evaluation/m1_v1/` | Reviewed aggregate metrics and Markdown only | Explicit staging after inspection |
| `tests/ml/` | Synthetic fixtures; optional local gold-hash check | Safe source |

No datasets are uploaded and there are no paid APIs, telemetry or cloud clients. Reports contain no customer text, vocabulary or example excerpts. Future changes must preserve this boundary. The optional real-gold hash test skips when the private file is absent; synthetic tests can run from a public checkout. Full preparation/training requires the original private files and exact approved hashes. Source redistribution rights have not been established by M1.

## Git checkpoint

No staging, commit, branch rename, remote setup or push is automatic. Never use `git add .` or `git add -A`. After reviewing source and aggregate reports, use the explicit paths in `CHECKPOINT.md`. Preserve `Decisions.md` locally; it is excluded from the proposed initial checkpoint because it contains historical customer excerpts.

## Scope after M1

FastAPI integration can reuse the inference class without importing the legacy scripts. Authentication, ticket persistence, customer and agent interfaces, knowledge retrieval, generation and policy enforcement require separate approval. Real Spotify account or financial operations are not implemented or authorized.
