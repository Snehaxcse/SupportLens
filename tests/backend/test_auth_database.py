"""Transaction ownership and fail-closed database dependency checks, offline."""
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from sqlalchemy.exc import OperationalError

from supportlens.backend.api.database import Database
from supportlens.backend.api.errors import APIError
from supportlens.backend.config import ConfigurationError


@pytest.mark.parametrize("readonly,fail_body", [(True, False), (False, False), (False, True)])
def test_transaction_owner_commits_or_rolls_back_and_checks_identity(settings_factory, monkeypatch, readonly, fail_body):
    from supportlens.backend.api import database as module
    events = []
    session = Mock()
    @contextmanager
    def session_context():
        events.append("session_open")
        yield session
        events.append("session_closed")
    class Connection:
        @contextmanager
        def begin(self):
            events.append("begin")
            try:
                yield
                events.append("commit")
            except Exception:
                events.append("rollback")
                raise
        def __enter__(self): return self
        def __exit__(self, *args): events.append("connection_closed")
    connection = Connection()
    engine = Mock()
    engine.connect.return_value = connection
    monkeypatch.setattr(module, "build_engine", lambda *args, **kwargs: engine)
    inspect = Mock(side_effect=lambda *args, **kwargs: events.append("identity_verified"))
    monkeypatch.setattr(module, "inspect_connection", inspect)
    monkeypatch.setattr(module, "session_factory", lambda bound: session_context)
    database = Database(settings_factory())
    def operation():
        with database.transaction(readonly=readonly):
            assert events == ["begin", "identity_verified", "session_open"]
            if fail_body:
                raise RuntimeError("synthetic body error")
    if fail_body:
        with pytest.raises(RuntimeError):
            operation()
        assert "rollback" in events and "commit" not in events
    else:
        operation()
        assert events[-2:] == ["commit", "connection_closed"]
    assert inspect.call_args.kwargs["require_readonly"] is readonly
    assert session.flush.call_count == (0 if readonly or fail_body else 1)


def test_identity_mismatch_never_opens_session(settings_factory, monkeypatch):
    from supportlens.backend.api import database as module
    from unittest.mock import MagicMock
    engine = MagicMock()
    monkeypatch.setattr(module, "build_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(module, "inspect_connection", Mock(side_effect=ConfigurationError("synthetic identity mismatch")))
    factory = Mock()
    monkeypatch.setattr(module, "session_factory", factory)
    with pytest.raises(APIError) as error:
        with Database(settings_factory()).transaction():
            pytest.fail("Identity mismatch allowed application access")
    assert error.value.status == 503
    factory.assert_not_called()


def test_connection_failures_do_not_expose_driver_details(settings_factory, monkeypatch):
    from supportlens.backend.api import database as module
    engine = Mock()
    engine.connect.side_effect = OperationalError("SYNTHETIC_PRIVATE_DETAIL", {}, Exception("SYNTHETIC_DRIVER_SECRET"))
    monkeypatch.setattr(module, "build_engine", lambda *a, **kw: engine)
    with pytest.raises(APIError) as error:
        with Database(settings_factory()).transaction():
            pytest.fail("Connection should fail")
    assert error.value.status == 503 and "SYNTHETIC" not in str(error.value)


def test_production_api_database_is_rejected_before_engine_creation(settings_factory, monkeypatch):
    from supportlens.backend.api import database as module
    constructor = Mock()
    monkeypatch.setattr(module, "build_engine", constructor)
    with pytest.raises(ConfigurationError):
        Database(settings_factory(environment="production"))
    constructor.assert_not_called()
