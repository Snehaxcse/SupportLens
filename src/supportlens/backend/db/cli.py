"""Secret-safe M2-A command boundary. No reset, downgrade or production command."""
import argparse
import json
import logging
from pathlib import Path

from ..config import ConfigurationError, Environment, load_settings
from .migrations import migrate, verify_databases
from .preflight import preflight


def main(argv=None):
    parser = argparse.ArgumentParser(description="Guarded SupportLens M2-A database foundation")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[4])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("config-check")
    sub.add_parser("preflight")
    migration = sub.add_parser("migrate")
    migration.add_argument("--target", choices=["development", "test"], required=True)
    sub.add_parser("verify")
    args = parser.parse_args(argv)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
    logging.getLogger("psycopg").setLevel(logging.ERROR)
    try:
        settings = load_settings()
        if settings.environment == Environment.PRODUCTION:
            raise ConfigurationError("M2-A commands never access production databases.")
        if args.command == "config-check":
            result = {"status": "PASS", "environment": settings.environment.value,
                      "credentials_logged": False, "tls_mode": "verify-full",
                      "note": "Configuration checks do not establish server identity or connectivity."}
        elif args.command == "preflight":
            checked = preflight(settings, require_empty_test=True)
            result = {"status": "PASS", "databases": {key.value: value.public_report() for key, value in checked.items()}}
        elif args.command == "migrate":
            result = migrate(settings, Environment(args.target), args.root.resolve())
        else:
            result = verify_databases(settings)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except ConfigurationError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc), "credentials_logged": False}))
        return 2
    except Exception:
        print(json.dumps({"status": "BLOCKED", "reason": "Database operation failed; details suppressed to protect private configuration."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
