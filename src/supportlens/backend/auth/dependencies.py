"""Reusable identity and database-backed role dependencies."""
import re

from fastapi import Depends, Request

from ..api.errors import APIError, unauthorized
from ..domain.enums import UserRole


def verified_identity(request: Request):
    values = request.headers.getlist("authorization")
    if len(values) != 1:
        raise unauthorized()
    match = re.fullmatch(r"(?i:Bearer) ([A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)", values[0])
    if not match or len(values[0]) > 16384:
        raise unauthorized()
    return request.app.state.runtime.verifier.verify(match.group(1))


def current_user(request: Request, identity=Depends(verified_identity)):
    return request.app.state.runtime.users.current(identity.uid)


def customer_only(user=Depends(current_user)):
    if user.role != UserRole.CUSTOMER:
        raise APIError(403, "forbidden", "Customer access is required.")
    return user


def agent_only(user=Depends(current_user)):
    if user.role != UserRole.AGENT:
        raise APIError(403, "forbidden", "Agent access is required.")
    return user
