"""M2-B real PostgreSQL behavior, inside the existing guarded rollback-only workflow."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from queue import Queue
import os
import time
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from supportlens.backend.api.errors import APIError
from supportlens.backend.auth.admin import promote
from supportlens.backend.auth.audit import PromotionJournal
from supportlens.backend.auth.firebase import VerifiedIdentity
from supportlens.backend.auth.users import onboard_user
from supportlens.backend.config import Environment, load_settings
from supportlens.backend.db.models import Ticket, User
from supportlens.backend.db.preflight import inspect_connection
from supportlens.backend.db.session import build_engine
from supportlens.backend.db.testing import require_test_opt_in
from supportlens.backend.domain.enums import UserRole
from .test_postgresql_integration import verified_test_connection

pytestmark = [pytest.mark.db_integration, pytest.mark.skipif(
    os.environ.get("SUPPORTLENS_RUN_DB_TESTS") != "1", reason="Explicit verified PostgreSQL test opt-in is absent.")]


def synthetic_identity():
    return VerifiedIdentity("synthetic_m2b_" + uuid.uuid4().hex, "synthetic@example.invalid", "Synthetic M2-B")


def test_live_onboarding_profile_roles_inactivity_and_rollback(verified_test_connection, tmp_path):
    connection = verified_test_connection
    identity = synthetic_identity()
    with Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False) as session:
        first = onboard_user(session, identity)
        repeated = onboard_user(session, identity)
        assert first.id == repeated.id and first.role == UserRole.CUSTOMER
        assert session.scalar(select(func.count()).select_from(User).where(User.firebase_uid == identity.uid)) == 1

        class TestTransactionAdapter:
            target = SimpleNamespace(value="test")
            @contextmanager
            def transaction(self):
                with session.begin_nested():
                    yield session  # Savepoint only; original fixture always rolls back.

        promote(TestTransactionAdapter(), first.id, journal=PromotionJournal(tmp_path / "synthetic-audit.jsonl"),
                operator="synthetic-test-operator", reason="Synthetic test; outer transaction rolls back")
        assert onboard_user(session, identity).role == UserRole.AGENT
        user = session.get(User, first.id)
        user.is_active = False
        session.flush()
        with pytest.raises(APIError) as inactive:
            with session.begin_nested():
                onboard_user(session, identity)
        assert inactive.value.code == "account_inactive"
        user.is_active = True
        session.flush()

        with pytest.raises(RuntimeError):
            with session.begin_nested():
                onboard_user(session, VerifiedIdentity(identity.uid, "changed@example.invalid", "Changed"))
                raise RuntimeError("Synthetic failure after flush")
        session.expire_all()
        assert session.get(User, first.id).email == identity.email
        with pytest.raises(APIError):
            onboard_user(session, VerifiedIdentity("synthetic_missing_email"))

        owner = onboard_user(session, synthetic_identity())
        session.execute(insert(Ticket).values(customer_id=owner.id, subject="Synthetic history", description="Synthetic only"))
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.execute(update(User).where(User.id == owner.id).values(role=UserRole.AGENT))
        session.expire_all()
        assert session.get(User, owner.id).role == UserRole.CUSTOMER


def test_live_concurrent_first_login_waits_for_uid_conflict_and_rolls_back(verified_test_connection):
    connection = verified_test_connection
    settings = load_settings()
    require_test_opt_in(settings)
    identity = synthetic_identity()
    engine = build_engine(settings, Environment.TEST)
    worker_pid = Queue()
    first = Session(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
    pool = ThreadPoolExecutor(max_workers=1)

    def competing_login():
        with engine.connect() as contender:
            transaction = contender.begin()
            try:
                inspect_connection(contender, settings.spec(Environment.TEST), require_readonly=False)
                worker_pid.put(contender.execute(text("SELECT pg_backend_pid()")).scalar_one())
                with Session(bind=contender, join_transaction_mode="create_savepoint") as session:
                    result = onboard_user(session, identity)
                    assert session.scalar(select(func.count()).select_from(User).where(User.firebase_uid == identity.uid)) == 1
                    # Roll back this session's savepoint, then the owning outer transaction.
                    session.rollback()
                    return result
            finally:
                transaction.rollback()

    try:
        original = onboard_user(first, identity)
        owner_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        future = pool.submit(competing_login)
        contender_pid = worker_pid.get(timeout=30)
        deadline = time.monotonic() + 7
        blocked = False
        while time.monotonic() < deadline:
            blocked = connection.execute(text("SELECT :owner = ANY(pg_blocking_pids(:contender))"),
                                         {"owner": owner_pid, "contender": contender_pid}).scalar_one()
            if blocked:
                break
            time.sleep(0.05)
        assert blocked, "Competing insert must wait on the unique Firebase UID."
        first.rollback()  # Release this synthetic insert; never commit user data.
        second = future.result(timeout=30)
        assert second.role == UserRole.CUSTOMER and second.id != original.id
        assert connection.execute(select(func.count()).select_from(User).where(User.firebase_uid == identity.uid)).scalar_one() == 0
    finally:
        first.rollback()
        first.close()
        pool.shutdown(wait=True, cancel_futures=True)
        engine.dispose()
