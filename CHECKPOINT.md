# M1 manual Git checkpoint

No files have been staged or committed. No branch rename, remote creation or push has been performed. The current branch is unborn `master`.

## Reviewed intended paths

Only the following explicit paths belong to the proposed checkpoint. The three directories below have been inspected and contain source/synthetic tests only; ignored caches and egg-info are excluded.

```text
.gitignore
.python-version
pyproject.toml
constraints.txt
requirements-dev.txt
README.md
CHECKPOINT.md
src/supportlens/__init__.py
src/supportlens/ml/__init__.py
src/supportlens/ml/cli.py
src/supportlens/ml/config.py
src/supportlens/ml/data.py
src/supportlens/ml/evaluate.py
src/supportlens/ml/grouping.py
src/supportlens/ml/inference.py
src/supportlens/ml/io.py
src/supportlens/ml/split.py
src/supportlens/ml/train.py
tests/ml/conftest.py
tests/ml/test_cli_integrity.py
tests/ml/test_data.py
tests/ml/test_grouping_split.py
tests/ml/test_training_inference.py
scripts/01_inspect_data.py
scripts/02_inspect_data.py
scripts/03_extract_spotify_pairs.py
scripts/04_inspect_threads.py
scripts/05_build_spotify_threads.py
scripts/06_create_taxonomy_sample.py
scripts/07_prepare_gold_annotations.py
scripts/08_analyze_annotations.py
scripts/09_mine_balanced_candidates.py
scripts/10_merge_balanced_annotations.py
scripts/11_mine_final_candidates.py
scripts/12_merge_final_annotations.py
evaluation/m1_v1/results.json
evaluation/m1_v1/report.md
```

The old scripts are included as preserved source, not as safe commands to rerun. Several overwrite annotations or print private text. No dataset is included with them.

## Exclusions

- All `data/raw/`, `data/processed/`, `data/derived/`, and `data/splits/` files.
- All model binaries and local metadata under `artifacts/`.
- Environments, dependency directories, generated package metadata, caches, logs, keys, environment variants, databases and temporary outputs.
- `Decisions.md`: preserved locally and deliberately omitted because it contains historical customer excerpts. It remains untracked; do not include it in a broad staging command.

Evaluation JSON and Markdown contain aggregate metrics only. No messages, record IDs, feature vocabulary or error excerpts belong in these public files. Review them again if regenerated.

## Commands for the owner

Run from the repository root. The dry run does not stage files:

```powershell
git --no-optional-locks status --short --branch
git add --dry-run -- .gitignore .python-version pyproject.toml constraints.txt requirements-dev.txt README.md CHECKPOINT.md src/supportlens tests/ml scripts evaluation/m1_v1/results.json evaluation/m1_v1/report.md
```

After reviewing that exact file list, the owner may stage and inspect:

```powershell
git add -- .gitignore .python-version pyproject.toml constraints.txt requirements-dev.txt README.md CHECKPOINT.md src/supportlens tests/ml scripts evaluation/m1_v1/results.json evaluation/m1_v1/report.md
git diff --cached --stat
git diff --cached --name-only
git diff --cached --check
```

Verify no private data, model files, credentials or unexpected files appear before committing:

```powershell
git commit -m "feat(ml): establish reproducible intent classifier baseline"
```

Optional branch rename, only if the owner wants `main`:

```powershell
git branch -m main
```

No remote or push command is proposed because none is configured or authorized. Never use `git add .` or `git add -A` for this checkpoint.

## Verified M1 result

The local suite passed 65 tests. The original gold hash is unchanged. Preparation reproduced the same split without overwriting it. Training selected C=1 from validation only. Final primary test size is 35; accuracy is 0.6571 and macro-F1 is 0.6227, compared with majority accuracy 0.2571 and macro-F1 0.0584. Results are preliminary. APP_TECHNICAL test recall is zero; model scores do not permit automated handling.

Full private-data integrity and model-load verification are available with:

```powershell
.\.venv\Scripts\python.exe -B -m supportlens.ml.cli verify
```
