"""Local M1 commands. Existing annotation scripts are never imported or run."""
import argparse
import io
from pathlib import Path

from .config import DATA_VERSION, GOLD_SHA256, GROUPING_VERSION, NEAR_DUPLICATE_THRESHOLD, paths
from .data import normalize_flag, read_csv_strings, validate_and_derive
from .grouping import group_records, read_parents
from .inference import IntentClassifier
from .io import (
    IntegrityError, assert_protected, canonical_json, digest_json, protected_hashes,
    read_json, save_json_once, save_once, sha256,
)
from .split import assert_no_leakage, attach_eligibility, create_split, persist_split
from .train import train_and_evaluate


def require_gold(path: Path) -> None:
    if sha256(path) != GOLD_SHA256:
        raise IntegrityError("Gold SHA-256 differs from the approved source. No dataset operation is permitted.")


def prepare(root: Path) -> dict:
    p = paths(root)
    require_gold(p["gold"])
    snapshot = protected_hashes(root)
    snapshot_file = p["derived"] / "protected_sources.json"
    if snapshot_file.exists() and read_json(snapshot_file) != snapshot:
        raise IntegrityError("Protected sources differ from the established data version.")
    gold = read_csv_strings(p["gold"])
    threads = read_csv_strings(p["threads"])
    derived, validation = validate_and_derive(gold, threads, GOLD_SHA256)
    parents = read_parents(p["raw"])
    groups, grouping = group_records(threads, derived, parents, validation["near_duplicate_links"])
    del parents
    frame = attach_eligibility(derived, groups)
    source_metadata = {
        "data_version": DATA_VERSION, "gold_sha256": GOLD_SHA256,
        "threads_sha256": snapshot["data/processed/spotify_threads.csv"],
        "raw_sha256": snapshot["data/raw/twcs.csv"], "grouping_version": GROUPING_VERSION,
        "near_duplicate_threshold": NEAR_DUPLICATE_THRESHOLD,
        "primary_flag_policy": "Both annotation flags false; complete ancestry components; seven core labels only.",
    }
    frame, manifest = create_split(frame, source_metadata)
    summary = {
        "status": validation["status"], "gold_sha256": GOLD_SHA256,
        "rows": len(frame), "correction_count": len(validation["corrections"]),
        "unresolved_damage_count": len(validation["exclusions"]),
        "label_counts": validation["label_counts"], "flag_counts": validation["flag_counts"],
        "duplicates": validation["duplicates"], "grouping": grouping,
        "primary_eligible": int(frame["training_eligible"].sum()),
        "primary_class_counts": frame.loc[frame["training_eligible"], "intent"].value_counts().sort_index().to_dict(),
        "sources_preserved": True,
    }
    csv_output = io.StringIO()
    frame.to_csv(csv_output, index=False, lineterminator="\n")
    # All validation and support gates pass before writing any new dataset artifact.
    assert_protected(root, snapshot)
    save_json_once(snapshot_file, snapshot)
    save_once(p["derived"] / "dataset.csv", csv_output.getvalue())
    save_json_once(p["derived"] / "validation.json", validation)
    save_json_once(p["derived"] / "grouping.json", {"summary": grouping, "records": groups.to_dict(orient="records")})
    save_json_once(p["derived"] / "summary.json", summary)
    persist_split(p["splits"], manifest)
    artifact_paths = {
        "dataset": p["derived"] / "dataset.csv", "validation": p["derived"] / "validation.json",
        "grouping": p["derived"] / "grouping.json", "summary": p["derived"] / "summary.json",
        "membership": p["splits"] / "membership.json",
    }
    save_json_once(p["derived"] / "preparation.json", {
        "data_version": DATA_VERSION, "gold_sha256": GOLD_SHA256,
        "files": {key: {"path": path.relative_to(root).as_posix(), "sha256": sha256(path)} for key, path in artifact_paths.items()},
    })
    return summary


def load_prepared(root: Path):
    p = paths(root)
    require_gold(p["gold"])
    assert_protected(root, read_json(p["derived"] / "protected_sources.json"))
    preparation = read_json(p["derived"] / "preparation.json")
    # Resolve only expected files; never trust arbitrary paths from a manifest.
    expected = {"dataset": p["derived"] / "dataset.csv", "validation": p["derived"] / "validation.json", "grouping": p["derived"] / "grouping.json", "summary": p["derived"] / "summary.json", "membership": p["splits"] / "membership.json"}
    for key, path in expected.items():
        entry = preparation["files"][key]
        if entry["path"] != path.relative_to(root).as_posix() or sha256(path) != entry["sha256"]:
            raise IntegrityError("Prepared artifact integrity check failed.")
    frame = read_csv_strings(p["derived"] / "dataset.csv")
    for column in ("needs_context", "language_issue", "core_intent", "training_eligible", "group_quarantined"):
        frame[column] = frame[column].map(normalize_flag)
    frame["corpus_component_size"] = frame["corpus_component_size"].astype(int)
    manifest = read_json(p["splits"] / "membership.json")
    if digest_json(manifest["records"]) != manifest["metadata"]["membership_sha256"]:
        raise IntegrityError("Split membership checksum failed.")
    columns = list(manifest["records"][0])
    actual_records = frame[columns].to_dict(orient="records")
    if actual_records != manifest["records"]:
        raise IntegrityError("Derived dataset and persisted split membership differ.")
    assert_no_leakage(frame)
    return frame, manifest, preparation


def train(root: Path) -> dict:
    p = paths(root)
    frame, manifest, preparation = load_prepared(root)
    source_directory = Path(__file__).parent
    provenance = {
        "gold_sha256": GOLD_SHA256,
        "derived_dataset_sha256": preparation["files"]["dataset"]["sha256"],
        "split_membership_sha256": manifest["metadata"]["membership_sha256"],
        "source_code_sha256": digest_json({file.name: sha256(file) for file in sorted(source_directory.glob("*.py"))}),
        "constraints_sha256": sha256(root / "constraints.txt"),
        "data_integrity_summary": read_json(p["derived"] / "summary.json"),
    }
    result = train_and_evaluate(frame, manifest, p["models"], p["evaluation"], provenance)
    assert_protected(root, read_json(p["derived"] / "protected_sources.json"))
    return result


def verify(root: Path) -> dict:
    p = paths(root)
    frame, manifest, _ = load_prepared(root)
    model = IntentClassifier(p["models"])
    prediction = model.predict("Synthetic example: the music player stops unexpectedly.")
    if not prediction["requires_human_review"] or prediction["automated_actions_allowed"]:
        raise IntegrityError("Inference human-review gate failed.")
    completed = read_json(p["models"] / "completed_run.json")
    for name, expected in completed["model_files"].items():
        if name not in ("model.joblib", "metadata.json") or sha256(p["models"] / name) != expected:
            raise IntegrityError("Completed model artifact changed.")
    for name, expected in completed["evaluation_files"].items():
        if name not in ("results.json", "report.md") or sha256(p["evaluation"] / name) != expected:
            raise IntegrityError("Completed report changed.")
    return {"gold_hash_unchanged": True, "all_protected_hashes_unchanged": True,
            "rows": len(frame), "split_membership_sha256": manifest["metadata"]["membership_sha256"],
            "leakage_checks": manifest["metadata"]["leakage_checks"], "artifact_loads": True,
            "human_review_default": True, "automated_actions_allowed": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local SupportLens M1; private data stays on this computer.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "train", "verify"):
        commands.add_parser(command)
    predict_parser = commands.add_parser("predict")
    predict_parser.add_argument("--message", required=True, help="Use synthetic text for CLI demonstrations; input is never echoed.")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        if args.command == "prepare":
            result = prepare(root)
        elif args.command == "train":
            report = train(root)
            result = {"selected_C": report["selected_C"], "validation": report["validation"], "test": report["test"], "preliminary": True}
        elif args.command == "verify":
            result = verify(root)
        else:
            result = IntentClassifier(paths(root)["models"]).predict(args.message)
        print(canonical_json(result), end="")
        return 0
    except IntegrityError as exc:
        print("M1 integrity gate failed: " + str(exc))
        return 2
    except (OSError, ValueError, KeyError):
        print("M1 operation failed: missing or invalid local input/artifact. No customer text is logged.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
