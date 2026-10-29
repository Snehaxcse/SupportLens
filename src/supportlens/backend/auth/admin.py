"""Trusted local promotion only. No HTTP route and no automated role assignment."""
import argparse
import getpass
import json
from pathlib import Path
import sys
import uuid

from sqlalchemy import select

from ..api.database import Database
from ..config import ConfigurationError, Environment, load_settings
from ..db.migrations import verify_databases
from ..db.models import User
from ..domain.enums import UserRole
from .audit import PromotionJournal


class PromotionBlocked(RuntimeError):
    pass


def promote(database, user_id, *, journal, operator, reason):
    if (not operator.strip() or len(operator) > 120 or not reason.strip() or len(reason) > 240
            or any(ord(c) < 32 for c in operator + reason)):
        raise PromotionBlocked("A bounded operator label and reason are required.")
    operation_id = str(uuid.uuid4())
    with journal.locked():
        intent_written = False
        committed = False
        try:
            with database.transaction() as session:
                user = session.scalars(select(User).where(User.id == user_id).with_for_update()).one_or_none()
                if user is None or not user.is_active or user.role != UserRole.CUSTOMER:
                    raise PromotionBlocked("Target must be one existing active customer.")
                journal.append("promotion_intent", operation_id=operation_id, user_id=str(user_id),
                    operator=operator, reason=reason, database=database.target.value,
                    from_role="CUSTOMER", to_role="AGENT")
                intent_written = True
                user.role = UserRole.AGENT
                session.flush()  # Existing support-history guards stay authoritative.
            committed = True
            journal.append("promotion_committed", operation_id=operation_id, user_id=str(user_id))
        except Exception:
            if intent_written:
                try:
                    journal.append("promotion_reconciliation_required" if committed else "promotion_not_confirmed",
                                   operation_id=operation_id, user_id=str(user_id))
                except Exception:
                    pass  # Durable intent remains; never pretend the two stores are atomic.
            raise PromotionBlocked("Promotion was not fully confirmed. Preserve the journal and reconcile database state read-only before retrying.") from None
    return operation_id


def main(argv=None):
    parser = argparse.ArgumentParser(description="Local administrator-controlled customer-to-agent promotion.")
    parser.add_argument("--target", choices=("development", "test"), required=True)
    parser.add_argument("--user-id", type=uuid.UUID, required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--audit-file", type=Path, required=True)
    args = parser.parse_args(argv)
    database = None
    try:
        settings = load_settings()
        target = Environment(args.target)
        if settings.environment != target:
            raise ConfigurationError("Explicit target must match ENVIRONMENT.")
        verify_databases(settings)
        database = Database(settings)
        with database.transaction(readonly=True) as session:
            user = session.scalars(select(User).where(User.id == args.user_id)).one_or_none()
            if user is None or not user.is_active or user.role != UserRole.CUSTOMER:
                raise PromotionBlocked("Target must be one existing active customer.")
        expected = "PROMOTE " + str(args.user_id)
        if input("Type " + expected + " to confirm: ") != expected:
            raise PromotionBlocked("Confirmation did not match; no role changed.")
        operation = promote(database, args.user_id, journal=PromotionJournal(args.audit_file),
                            operator=getpass.getuser(), reason=args.reason)
        print(json.dumps({"status": "promoted", "user_id": str(args.user_id), "operation_id": operation}))
        return 0
    except (Exception, KeyboardInterrupt):
        # No usernames, credential paths, DB URLs, SDK or driver errors on stdout.
        print(json.dumps({"status": "not_confirmed", "message": "No confirmed promotion. Review private audit records and database state before retrying."}), file=sys.stderr)
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
