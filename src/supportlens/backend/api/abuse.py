"""Bounded, single-process API budgets keyed by the verified actor or peer."""
from math import ceil
from threading import Lock
from time import monotonic

from .errors import APIError


def route_budget(method, route):
    if route.endswith("/onboard"):
        return "onboard", 8
    if route.endswith("/drafts") and method == "POST":
        return "draft", 12
    if route.endswith("/retrieval-preview") or route.endswith("/tickets/{ticket_id}/knowledge"):
        return "retrieval", 20
    if route.endswith("/knowledge") and method == "POST" or "/knowledge/{article_id}" in route and method == "PATCH":
        return "article_write", 10
    if route.endswith("/messages") and method == "POST":
        return "message", 30
    if route == "/api/v1/tickets" and method == "POST":
        return "ticket_create", 12
    return None


class AbuseLimits:
    def __init__(self, ip_limit, actor_limit, *, capacity=8192, clock=monotonic):
        self.ip_limit, self.actor_limit = ip_limit, actor_limit
        self.capacity, self.clock = capacity, clock
        self.windows = {}
        self.lock = Lock()

    def consume(self, key, limit):
        now = self.clock()
        with self.lock:
            count, deadline = self.windows.get(key, (0, now))
            if now >= deadline:
                if key not in self.windows and len(self.windows) >= self.capacity:
                    self.windows = {key: window for key, window in self.windows.items() if window[1] > now}
                    if len(self.windows) >= self.capacity:
                        raise self.denied(60)
                count, deadline = 0, now + 60
            if count >= limit:
                raise self.denied(max(1, ceil(deadline - now)))
            self.windows[key] = count + 1, deadline

    @staticmethod
    def denied(seconds):
        return APIError(429, "rate_limited", "Too many requests. Wait before trying again.", retry_after=seconds)

    def peer(self, request):
        self.consume(("peer", request.client.host if request.client else "unknown"), self.ip_limit)

    def readiness(self, request):
        self.consume(("readiness_peer", request.client.host if request.client else "unknown"), 20)
        self.consume(("readiness_total",), 60)

    def actor(self, request, uid):
        self.consume(("actor", uid), self.actor_limit)
        budget = route_budget(request.method, getattr(request.scope.get("route"), "path", ""))
        if budget:
            bucket, limit = budget
            self.consume((bucket, uid), limit)
