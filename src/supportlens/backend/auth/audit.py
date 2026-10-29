"""Private append-only operator journal; no ticket-shaped fake audit records.

An intent is fsynced before mutation. A result is fsynced after commit. Filesystem
and PostgreSQL commits cannot be atomic: missing/unknown results require manual
read-only reconciliation. The hash chain detects accidental alteration, not an
administrator who can rewrite the file. Protect the directory with OS ACLs.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat


class AuditUnavailable(RuntimeError):
    pass


def digest(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class PromotionJournal:
    def __init__(self, path):
        self.path = Path(path)
        if not self.path.is_absolute() or any(part.is_symlink() for part in [self.path, *self.path.parents]):
            raise AuditUnavailable("Audit requires an absolute, non-symlink private path.")
        repository = Path(__file__).resolve().parents[4]
        if self.path.is_relative_to(repository) and not self.path.is_relative_to(repository / "artifacts"):
            raise AuditUnavailable("In-repository audit files must stay under ignored artifacts/.")

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+", encoding="utf-8", newline="\n") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise AuditUnavailable("Audit destination must be a regular file.")
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                if stat.S_IMODE(os.fstat(stream.fileno()).st_mode) & 0o077:
                    raise AuditUnavailable("Audit file permissions must be private.")
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                previous = "0" * 64
                stream.seek(0)
                for line in stream:
                    try:
                        if not line.endswith("\n"):
                            raise ValueError
                        record = json.loads(line)
                        saved_hash = record.pop("hash")
                        if (record.get("format") != "supportlens-promotion-v1"
                                or record["previous_hash"] != previous or digest(record) != saved_hash):
                            raise ValueError
                        previous = saved_hash
                    except (ValueError, KeyError, TypeError, AttributeError):
                        raise AuditUnavailable("Existing audit journal is invalid; preserve it for review.") from None
                self._previous = previous
                self._stream = stream
                stream.seek(0, os.SEEK_END)
                yield self
            finally:
                self._stream = None
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    def append(self, event, **fields):
        record = {"format": "supportlens-promotion-v1", "event": event, "recorded_at": datetime.now(timezone.utc).isoformat(),
                  "previous_hash": self._previous, **fields}
        record["hash"] = digest(record)
        self._stream.write(json.dumps(record, sort_keys=True) + "\n")
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._previous = record["hash"]
