"""Fresh, bounded dependency checks with at most one outstanding probe per dependency."""
import asyncio
from concurrent.futures import Future
from threading import Thread


class Readiness:
    def __init__(self, checks, timeout):
        self.checks = checks
        self.timeout = timeout
        self.inflight = {}
        self.last = {name: "unknown" for name in checks}
        self.stopping = False

    @staticmethod
    def probe(check, result):
        try:
            check()
        except Exception:
            result.set_result("unavailable")
        else:
            result.set_result("ready")

    async def verify(self):
        if self.stopping:
            return False
        pending = {}
        for name, check in self.checks.items():
            result = self.inflight.get(name)
            if result is None or result.done():
                result = Future()
                self.inflight[name] = result
                Thread(target=self.probe, args=(check, result), name=f"supportlens-probe-{name}", daemon=True).start()
            pending[name] = asyncio.wrap_future(result)
        completed, _ = await asyncio.wait(pending.values(), timeout=self.timeout)
        self.last = {name: task.result() if task in completed else "timeout" for name, task in pending.items()}
        return all(status == "ready" for status in self.last.values())

    def stop(self):
        self.stopping = True
