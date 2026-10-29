"""Local promotion safety and durable-journal behavior with synthetic identities."""
from contextlib import contextmanager
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
import uuid

import pytest

from supportlens.backend.auth.admin import PromotionBlocked, main, promote
from supportlens.backend.auth.audit import AuditUnavailable, PromotionJournal
from supportlens.backend.domain.enums import UserRole


class MemoryDatabase:
    def __init__(self, user, *, fail_flush=False, fail_commit=False):
        self.user = user
        self.target = SimpleNamespace(value="test")
        self.fail_flush, self.fail_commit = fail_flush, fail_commit
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def transaction(self):
        previous = self.user.role if self.user is not None else None
        session = Mock()
        session.scalars.return_value.one_or_none.return_value = self.user
        if self.fail_flush:
            session.flush.side_effect = RuntimeError("synthetic support-history guard")
        try:
            yield session
            if self.fail_commit:
                raise RuntimeError("synthetic commit failure")
            self.committed = True
        except Exception:
            if self.user is not None:
                self.user.role = previous
            self.rolled_back = True
            raise


@pytest.fixture
def customer():
    return SimpleNamespace(id=uuid.uuid4(), role=UserRole.CUSTOMER, is_active=True)


def test_local_promotion_records_durable_intent_and_result(tmp_path, customer):
    path = tmp_path / "private" / "promotion.jsonl"
    database = MemoryDatabase(customer)
    operation = promote(database, customer.id, journal=PromotionJournal(path), operator="synthetic-operator", reason="Synthetic approval")
    assert customer.role == UserRole.AGENT and database.committed
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [row["event"] for row in records] == ["promotion_intent", "promotion_committed"]
    assert all(row["operation_id"] == operation and row["user_id"] == str(customer.id) for row in records)
    with PromotionJournal(path).locked():
        pass  # The stored hash chain validates.


@pytest.mark.parametrize("failure", ["flush", "commit"])
def test_database_failure_rolls_back_and_leaves_reconciliation_evidence(tmp_path, customer, failure):
    path = tmp_path / "promotion.jsonl"
    database = MemoryDatabase(customer, **{"fail_" + failure: True})
    with pytest.raises(PromotionBlocked):
        promote(database, customer.id, journal=PromotionJournal(path), operator="synthetic", reason="Synthetic")
    assert database.rolled_back and customer.role == UserRole.CUSTOMER
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["event"] for r in records] == ["promotion_intent", "promotion_not_confirmed"]


def test_audit_intent_failure_prevents_privilege_change(tmp_path, customer, monkeypatch):
    journal = PromotionJournal(tmp_path / "promotion.jsonl")
    monkeypatch.setattr(journal, "append", Mock(side_effect=OSError("synthetic disk failure")))
    database = MemoryDatabase(customer)
    with pytest.raises(PromotionBlocked):
        promote(database, customer.id, journal=journal, operator="synthetic", reason="Synthetic")
    assert customer.role == UserRole.CUSTOMER and database.rolled_back


def test_failed_post_commit_audit_is_not_reported_as_success(tmp_path, customer, monkeypatch):
    journal = PromotionJournal(tmp_path / "promotion.jsonl")
    append = journal.append
    def fail_result(event, **fields):
        if event != "promotion_intent":
            raise OSError("synthetic disk failure after commit")
        append(event, **fields)
    monkeypatch.setattr(journal, "append", fail_result)
    database = MemoryDatabase(customer)
    with pytest.raises(PromotionBlocked, match="reconcile"):
        promote(database, customer.id, journal=journal, operator="synthetic", reason="Synthetic")
    assert customer.role == UserRole.AGENT and database.committed
    assert len(journal.path.read_text().splitlines()) == 1  # Durable pending intent remains.


@pytest.mark.parametrize("kind", ["unknown", "inactive", "agent"])
def test_promotion_requires_existing_active_customer(tmp_path, customer, kind):
    if kind == "unknown":
        customer = None
    elif kind == "inactive":
        customer.is_active = False
    else:
        customer.role = UserRole.AGENT
    database = MemoryDatabase(customer)
    with pytest.raises(PromotionBlocked):
        promote(database, uuid.uuid4(), journal=PromotionJournal(tmp_path / "audit.jsonl"), operator="synthetic", reason="Synthetic")
    assert not database.committed


def test_unknown_or_damaged_audit_file_is_preserved(tmp_path):
    path = tmp_path / "existing.jsonl"
    path.write_text("Unexpected existing data\n")
    original = path.read_bytes()
    with pytest.raises(AuditUnavailable):
        with PromotionJournal(path).locked():
            pass
    assert path.read_bytes() == original


def test_journal_rejects_relative_path():
    with pytest.raises(AuditUnavailable):
        PromotionJournal("relative-audit.jsonl")


def test_cli_confirmation_required_before_any_promotion(monkeypatch, customer, tmp_path):
    from supportlens.backend.auth import admin
    settings = SimpleNamespace(environment=admin.Environment.TEST)
    monkeypatch.setattr(admin, "load_settings", lambda: settings)
    monkeypatch.setattr(admin, "verify_databases", Mock())
    database = MagicMock()
    database.transaction.return_value.__enter__.return_value.scalars.return_value.one_or_none.return_value = customer
    monkeypatch.setattr(admin, "Database", lambda settings: database)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    promotion = Mock()
    monkeypatch.setattr(admin, "promote", promotion)
    assert main(["--target", "test", "--user-id", str(customer.id), "--reason", "Synthetic",
                 "--audit-file", str(tmp_path / "private.jsonl")]) == 1
    promotion.assert_not_called()


def test_cli_rejects_environment_mismatch_before_connections(monkeypatch, tmp_path):
    from supportlens.backend.auth import admin
    monkeypatch.setattr(admin, "load_settings", lambda: SimpleNamespace(environment=admin.Environment.PRODUCTION))
    preflight = Mock()
    monkeypatch.setattr(admin, "verify_databases", preflight)
    assert main(["--target", "test", "--user-id", str(uuid.uuid4()), "--reason", "Synthetic",
                 "--audit-file", str(tmp_path / "private.jsonl")]) == 1
    preflight.assert_not_called()
