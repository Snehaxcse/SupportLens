"""Atomic, PostgreSQL-native onboarding. No client role is accepted."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from ..api.errors import APIError
from ..db.models import User
from ..domain.enums import UserRole


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    email: str
    display_name: str | None
    role: UserRole
    is_active: bool
    created_at: datetime


def snapshot(user):
    return CurrentUser(user.id, user.email, user.display_name, user.role, user.is_active, user.created_at)


def require_active(user):
    if user is None:
        raise APIError(403, "onboarding_required", "Complete authenticated onboarding first.")
    if not user.is_active:
        raise APIError(403, "account_inactive", "This account is inactive.")
    return user


def onboard_user(session, identity):
    if identity.email is None:
        raise APIError(422, "verified_email_required", "Verify an email address with Firebase before onboarding.")
    # Unique firebase_uid arbitrates concurrent first logins. A conflicting insert
    # waits for the winner; SELECT FOR UPDATE then serializes sync and promotions.
    session.execute(insert(User).values(firebase_uid=identity.uid, email=identity.email,
        display_name=identity.display_name, role=UserRole.CUSTOMER).on_conflict_do_nothing(
            index_elements=[User.firebase_uid]))
    user = session.scalars(select(User).where(User.firebase_uid == identity.uid).with_for_update()).one()
    require_active(user)
    user.email = identity.email
    if identity.display_name is not None:
        user.display_name = identity.display_name
    # Existing role and activation state are never synchronized from the token.
    session.flush()
    return snapshot(user)


class UserService:
    def __init__(self, database):
        self.database = database

    def current(self, uid):
        with self.database.transaction(readonly=True) as session:
            user = session.scalars(select(User).where(User.firebase_uid == uid)).one_or_none()
            return snapshot(require_active(user))

    def onboard(self, identity):
        with self.database.transaction() as session:
            result = onboard_user(session, identity)
        # Return only after the owning transaction successfully commits.
        return result
