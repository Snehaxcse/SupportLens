"""Exercise the release on loopback with fictional fixtures and stubbed Firebase."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter, process_time, sleep
from unittest.mock import patch

from dotenv import dotenv_values
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select, text

from supportlens.backend.api.app import create_app
from supportlens.backend.api.config import APISettings
from supportlens.backend.api.demo import ARTICLE_BODIES, ARTICLE_RIGHTS, ARTICLE_SOURCE, ARTICLE_TITLE, MESSAGES, TICKETS
from supportlens.backend.api.errors import unauthorized
from supportlens.backend.auth.firebase import VerifiedIdentity
from supportlens.backend.config import Environment, Settings, parse_database_url
from supportlens.backend.db.base import Base
from supportlens.backend.db.demo import initialize_demo
from supportlens.backend.db.migrations import CACHE_REVISION, verify_schema
from supportlens.backend.db.preflight import client_security, inspect_connection
from supportlens.backend.db.session import build_engine


CUSTOMER = "fixture_customer_m5b"
AGENT = "fixture_agent_m5b"
PROJECT = "fictional-demo-rehearsal"


class FixtureVerifier:
    def __init__(self, settings):
        assert settings.auth_project_id == PROJECT
        self.fail = False

    def readiness_probe(self):
        if self.fail:
            raise OSError("simulated Firebase outage")

    def verify(self, token):
        accounts = {"fake.customer.signature": CUSTOMER, "fake.agent.signature": AGENT}
        uid = accounts.get(token)
        if uid is None:
            raise unauthorized()
        return VerifiedIdentity(uid, "fixture@example.invalid")

    def close(self):
        pass


def inputs(folder):
    values = dotenv_values(folder / ".env.rehearsal")
    urls = {name: parse_database_url(SecretStr(values[name])) for name in (
        "DEMO_DATABASE_URL", "DEMO_MIGRATION_DATABASE_URL", "DEMO_RESTORE_DATABASE_URL")}
    assert all(url.host == "127.0.0.1" for url in urls.values())
    assert len({url.port for url in urls.values()}) == 1
    assert urls["DEMO_RESTORE_DATABASE_URL"].database == "SupportLens_demo_restore"
    opts = {"_env_file": None, "environment": "synthetic_demo",
            "demo_database_url": values["DEMO_DATABASE_URL"],
            "db_ssl_root_cert": str(folder / "rehearsal-ca.pem")}
    return values, urls, opts


def working_set_mib():
    if sys.platform != "win32":
        import resource

        return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2)
    import ctypes
    from ctypes import wintypes

    class Memory(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak", "current", "pp", "p", "pnp", "np", "page", "peakpage")]

    stats = Memory()
    stats.cb = ctypes.sizeof(stats)
    handle = ctypes.c_void_p(ctypes.windll.kernel32.GetCurrentProcess())
    if not ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(stats), stats.cb):
        raise OSError("Process memory measurement unavailable.")
    return round(stats.current / 1024**2, 2)


def await_classification(client, path, agent):
    start = perf_counter()
    while perf_counter() - start < 15:
        outcome = client.get(path + "/classification", headers=agent)
        assert outcome.status_code == 200
        if outcome.json()["status"] == "SUCCEEDED":
            return round((perf_counter() - start) * 1000, 2)
        sleep(0.15)
    raise AssertionError("Fictional classification did not complete.")


def publish_article(client, agent):
    body = {"title": ARTICLE_TITLE, "body": ARTICLE_BODIES[0], "source_type": "SYNTHETIC_TEST",
            "source_name": ARTICLE_SOURCE, "rights_reference": ARTICLE_RIGHTS}
    article = client.post("/api/v1/agent/knowledge", headers=agent, json=body)
    assert article.status_code == 201
    identifier, generation = (article.json()[key] for key in ("id", "generation"))
    path = "/api/v1/agent/knowledge/" + identifier
    publication = client.post(path + "/lifecycle", headers=agent,
                              json={"operation": "PUBLISH", "expected_generation": generation})
    assert publication.status_code == 200
    start = perf_counter()
    while perf_counter() - start < 20:
        current = client.get(path, headers=agent)
        assert current.status_code == 200
        if current.json()["index_state"] == "READY":
            return identifier, round((perf_counter() - start) * 1000, 2)
        sleep(0.3)
    raise AssertionError("Fictional indexing did not complete.")


def exercise(folder):
    values, _, opts = inputs(folder)
    migration = Settings(**opts, demo_migration_database_url=values["DEMO_MIGRATION_DATABASE_URL"])
    initialize_demo(migration, (CUSTOMER, AGENT), Path.cwd())
    private_fixture = folder / "fake-credential.json"
    private_fixture.write_text("{}", encoding="utf-8")
    settings = APISettings(**opts, demo_firebase_project_id=PROJECT, demo_customer_uid=CUSTOMER,
                           demo_agent_uid=AGENT, google_application_credentials=str(private_fixture),
                           cors_allowed_origins=("https://fixture-ui.example.invalid",),
                           demo_trusted_hosts=("testserver",))
    started, cpu_started, rss_before = perf_counter(), process_time(), working_set_mib()
    with patch("supportlens.backend.api.app.FirebaseVerifier", FixtureVerifier):
        app = create_app(settings)
    factory_ms = round((perf_counter() - started) * 1000, 2)
    customer = {"Authorization": "Bearer fake.customer.signature"}
    agent = {"Authorization": "Bearer fake.agent.signature"}
    timings = []
    with TestClient(app) as client:
        for _ in range(5):
            start = perf_counter()
            result = client.get("/ready")
            timings.append(round((perf_counter() - start) * 1000, 2))
            assert result.status_code == 200
        start = perf_counter()
        app.state.runtime.verifier.fail = True
        assert client.get("/ready").status_code == 503
        failed_ms = round((perf_counter() - start) * 1000, 2)
        app.state.runtime.verifier.fail = False
        assert client.get("/ready").status_code == 200
        assert client.get("/api/v1/me", headers=customer).json()["role"] == "CUSTOMER"
        assert client.get("/api/v1/me", headers=agent).json()["role"] == "AGENT"
        assert client.post("/api/v1/auth/onboard", headers=customer, json={}).status_code == 403
        subject, description = TICKETS[0]
        response = client.post("/api/v1/tickets", headers=customer, json={"subject": subject, "description": description})
        assert response.status_code == 201
        path = "/api/v1/agent/tickets/" + response.json()["id"]
        assert client.post(path + "/claim", headers=agent, json={}).status_code == 200
        assert client.post(path + "/messages", headers=agent,
                           json={"body": MESSAGES[1], "visibility": "PUBLIC"}).status_code == 201
        assert client.post(path + "/messages", headers=agent,
                           json={"body": MESSAGES[2], "visibility": "INTERNAL"}).status_code == 201
        public = client.get(path.replace("/agent", ""), headers=customer)
        assert public.status_code == 200 and MESSAGES[1] in public.text and MESSAGES[2] not in public.text
        assert client.get("/api/v1/agent/operations", headers=customer).status_code == 403
        await_classification(client, path, agent)
        article_id, index_ms = publish_article(client, agent)
        evidence = client.get(path + "/knowledge", headers=agent)
        assert evidence.status_code == 200
        assert any(item["article_id"] == article_id for item in evidence.json()["items"])
        assert client.get(path + "/drafts", headers=agent).json()["generation_enabled"] is False
        operations = client.get("/api/v1/agent/operations", headers=agent).json()
        assert operations["queue_states"]["classification"]["SUCCEEDED"] == 1
        assert operations["queue_states"]["gemini"]["reservations_today"] == 0
        worker = app.state.runtime.classification_worker
        worker.stop()
        assert client.get("/ready").status_code == 503
        subject, description = TICKETS[1]
        pending = client.post("/api/v1/tickets", headers=customer, json={"subject": subject, "description": description})
        assert pending.status_code == 201
        recovery_path = "/api/v1/agent/tickets/" + pending.json()["id"]
        assert client.get(recovery_path + "/classification", headers=agent).json()["status"] == "PENDING"
        worker.start()
        recovered_ms = await_classification(client, recovery_path, agent)
        assert client.get("/ready").status_code == 200
        with patch.object(app.state.runtime.database, "readiness_probe", side_effect=OSError):
            assert client.get("/ready").status_code == 503
        after_models = working_set_mib()
    record = {"status": "PASS", "app_factory_ms": factory_ms, "ready_ms": timings,
              "ready_median_ms": median(timings), "simulated_failure_ms": failed_ms,
              "worker_restart_ready": True, "pending_processed_after_restart_ms": recovered_ms,
              "embedding_index_ms": index_ms, "python_cpu_seconds": round(process_time() - cpu_started, 3),
              "working_set_mib_before": rss_before, "working_set_mib_with_models": after_models,
              "gemini_reservations": 0}
    (folder / "runtime.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


def snapshot(connection):
    hashes = {}
    for name, table in sorted(Base.metadata.tables.items()):
        rows = (hashlib.sha256(json.dumps(dict(row), default=str, sort_keys=True).encode()).hexdigest()
                for row in connection.execute(select(table)).mappings())
        hashes[name] = sorted(rows)
    return hashes


def verify_restore(folder):
    values, urls, opts = inputs(folder)
    settings = Settings(**opts, demo_migration_database_url=values["DEMO_MIGRATION_DATABASE_URL"])
    source = build_engine(settings, Environment.SYNTHETIC_DEMO, readonly=True)
    restore = create_engine(urls["DEMO_RESTORE_DATABASE_URL"].set(drivername="postgresql+psycopg"),
                            connect_args={"sslmode": "verify-full", "sslrootcert": str(folder / "rehearsal-ca.pem"),
                                          "channel_binding": "require", "connect_timeout": 3}, hide_parameters=True)
    try:
        with source.connect() as connection:
            identity = inspect_connection(connection, settings.spec(Environment.SYNTHETIC_DEMO, readonly=True))
            before = snapshot(connection)
        with restore.connect() as connection:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            target_name, target_oid = connection.execute(text(
                "SELECT current_database(), oid FROM pg_database WHERE datname=current_database()")).one()
            assert target_name == "SupportLens_demo_restore" and target_oid != identity.database_oid
            assert client_security(connection)[:2] == (True, "verify-full")
            schema = verify_schema(connection, required_revision=CACHE_REVISION)
            assert schema["guard_triggers_verified"] == 12 and snapshot(connection) == before
    finally:
        source.dispose()
        restore.dispose()
    result = {"status": "PASS", "revision": schema["revision"], "verified_guards": 12,
              "restored_rows": sum(map(len, before.values())), "verified_tables": len(before),
              "isolated_restore": True}
    (folder / "restore.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=("exercise", "verify"))
    parser.add_argument("folder", type=Path)
    options = parser.parse_args()
    try:
        (exercise if options.stage == "exercise" else verify_restore)(options.folder.resolve())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "stage": options.stage, "kind": type(exc).__name__}))
        raise SystemExit(1) from None
