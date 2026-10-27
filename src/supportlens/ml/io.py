"""Deterministic serialization and source immutability checks."""
import hashlib
import json
from pathlib import Path


class IntegrityError(ValueError):
    """An integrity or release gate failed; messages must not contain private text."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True, allow_nan=False) + "\n"


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def save_once(path: Path, content: str | bytes) -> None:
    """Never replace established artifacts, even if a CLI is accidentally rerun."""
    payload = content.encode("utf-8") if isinstance(content, str) else content
    if path.exists():
        if path.read_bytes() != payload:
            raise IntegrityError("Existing artifact differs; choose a reviewed new version.")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(payload)


def save_json_once(path: Path, value: object) -> None:
    save_once(path, canonical_json(value))


def read_json(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (OSError, ValueError) as exc:
        raise IntegrityError("Missing or invalid JSON artifact.") from exc


def protected_hashes(root: Path) -> dict[str, str]:
    protected = [root / "data/raw/twcs.csv", root / "Decisions.md"]
    protected += sorted((root / "scripts").glob("*.py"))
    protected += sorted((root / "data/processed").glob("*.csv"))
    return {p.relative_to(root).as_posix(): sha256(p) for p in protected}


def assert_protected(root: Path, expected: dict[str, str]) -> None:
    if protected_hashes(root) != expected:
        raise IntegrityError("A protected source changed; stop and inspect locally.")
