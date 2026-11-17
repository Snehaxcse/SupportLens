"""Credential-free release checks for GitHub Actions and local verification."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from urllib.request import urlopen


SIGNATURES = {
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    "github-token": re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{75,})"),
    "aws-key": re.compile(rb"(?:AKIA|ASIA)[A-Z0-9]{16}"),
    "google-service-account": re.compile(rb'"private_key"\s*:\s*"-----BEGIN'),
}
PRIVATE_PATH = re.compile(r"(^|/)(?:\.env(?:\.[^/]+)?|artifacts|node_modules|\.venv)(?:/|$)"
                          r"|\.(?:pem|p12|pfx|joblib|pkl|pickle|onnx)$", re.I)


def secrets():
    paths = subprocess.check_output(["git", "ls-files", "-z"]).decode().split("\0")
    findings = 0
    for path in filter(None, paths):
        if PRIVATE_PATH.search(path) and Path(path).name != ".env.example":
            print("Unexpected tracked private or generated path.")
            findings += 1
            continue
        content = Path(path).read_bytes()
        for label, pattern in SIGNATURES.items():
            if pattern.search(content):
                print(f"Potential {label} in a tracked file. Review the scoped secret scan.")
                findings += 1
    if findings:
        raise SystemExit(1)
    print("Tracked secret signature and filename checks passed.")


def package_advisories(package):
    name, version = package
    try:
        with urlopen(f"https://pypi.org/pypi/{quote(name)}/{quote(version)}/json", timeout=20) as response:
            advisories = json.load(response)["vulnerabilities"]
        return {"package": name, "version": version,
                "advisories": sorted(item["id"] for item in advisories if not item.get("withdrawn"))}
    except Exception as exc:
        return {"package": name, "version": version, "unverified": type(exc).__name__}


def dependencies():
    packages = sorted({(dist.metadata["Name"], dist.version) for dist in metadata.distributions()
                       if dist.metadata["Name"] and dist.metadata["Name"].lower() != "supportlens"})
    with ThreadPoolExecutor(max_workers=6) as executor:
        report = list(executor.map(package_advisories, packages))
    findings = [row for row in report if row.get("advisories") or row.get("unverified")]
    print(json.dumps({"public_versions_checked": len(packages), "findings": findings}))
    if findings:
        raise SystemExit(1)


def embeddings():
    from supportlens.backend.knowledge.embedding import DIRECTORY, FILES, MODEL_REVISION, verified_bytes

    DIRECTORY.mkdir(parents=True, exist_ok=True)
    remote = f"https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/{MODEL_REVISION}/"
    for name, (size, sha) in FILES.items():
        target = DIRECTORY / name
        if not target.exists():
            source = name if name == "tokenizer.json" else "onnx/" + name
            with urlopen(remote + source, timeout=40) as response:
                data = response.read(size + 1)
            if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
                raise SystemExit("Pinned embedding download failed its integrity check.")
            with target.open("xb") as file:
                file.write(data)
        verified_bytes(name)
    print("Pinned public tokenizer and ONNX model verified.")


def main():
    checks = {"secrets": secrets, "dependencies": dependencies, "embeddings": embeddings}
    parser = argparse.ArgumentParser()
    parser.add_argument("check", choices=tuple(checks))
    checks[parser.parse_args().check]()


if __name__ == "__main__":
    main()
