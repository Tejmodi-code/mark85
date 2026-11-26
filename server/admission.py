"""Per-process adaptive admission for short HTTP and live-table actions.

The controller provides a bounded, short wait under bursts. It adapts
its in-flight limit from observed completion latency and server failures. It does
not make game or wallet decisions; those remain transactional application rules.
"""
import asyncio
import math
import time


class AdaptiveAdmission:
    def __init__(self, hard_limit: int, *, queue_limit: int | None = None,
                 wait_seconds: float = 0.15, target_latency_ms: float = 350.0):
        if hard_limit < 1:
            raise ValueError("hard_limit must be positive")
        self.hard_limit = hard_limit
        self.min_limit = max(1, math.ceil(hard_limit / 4))
        self.current_limit = hard_limit
        self.queue_limit = queue_limit if queue_limit is not None else hard_limit * 2
        self.wait_seconds = max(0.0, wait_seconds)
        self.target_latency_ms = max(1.0, target_latency_ms)
        self.active = 0
        self.waiting = 0
        self.rejected = 0
        self.completed = 0
        self.ewma_latency_ms = 0.0
        self._healthy_samples = 0
        self._condition = asyncio.Condition()

    async def acquire(self) -> bool:
        async with self._condition:
            if self.active < self.current_limit and self.waiting == 0:
                self.active += 1
                return True
            if self.queue_limit <= 0 or self.waiting >= self.queue_limit:
                self.rejected += 1
                return False
            self.waiting += 1
            try:
                async with asyncio.timeout(self.wait_seconds):
                    while self.active >= self.current_limit:
                        await self._condition.wait()
                self.active += 1
                return True
            except TimeoutError:
                self.rejected += 1
                return False
            finally:
                self.waiting -= 1
                self._condition.notify_all()

    async def release(self, latency_ms: float, status_code: int = 200):
        async with self._condition:
            if self.active < 1:
                raise RuntimeError("admission slot released without a matching acquire")
            self.active -= 1
            self.completed += 1
            latency_ms = max(0.0, float(latency_ms))
            if self.completed == 1:
                self.ewma_latency_ms = latency_ms
            else:
                self.ewma_latency_ms = 0.2 * latency_ms + 0.8 * self.ewma_latency_ms
            if status_code >= 500 or self.ewma_latency_ms > self.target_latency_ms:
                self.current_limit = max(self.min_limit, math.floor(self.current_limit * 0.8))
                self._healthy_samples = 0
            elif self.ewma_latency_ms < self.target_latency_ms * 0.65:
                self._healthy_samples += 1
                if self._healthy_samples >= 32 and self.current_limit < self.hard_limit:
                    self.current_limit += 1
                    self._healthy_samples = 0
            else:
                self._healthy_samples = 0
            self._condition.notify_all()

    def snapshot(self) -> dict:
        return {"active": self.active, "queued": self.waiting,
                "limit": self.current_limit, "hard_limit": self.hard_limit,
                "completed": self.completed, "rejected": self.rejected,
                "ewma_latency_ms": round(self.ewma_latency_ms, 1)}


class AdaptiveAdmissionMiddleware:
    """Bound API concurrency per worker; give low traffic a direct path."""
    def __init__(self, app, *, controller: AdaptiveAdmission):
        self.app = app
        self.controller = controller

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        accepted = await self.controller.acquire()
        if not accepted:
            from starlette.responses import JSONResponse
            response = JSONResponse(
                {"detail": "Service is temporarily at capacity; retry shortly", "code": "admission_busy"},
                status_code=503, headers={"Retry-After": "1", "X-Admission-Decision": "defer"})
            await response(scope, receive, send)
            return
        status_code = 500
        async def tracked_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-admission-decision", b"admitted"))
                message = {**message, "headers": headers}
            await send(message)
        try:
            await self.app(scope, receive, tracked_send)
        finally:
            await self.controller.release((time.perf_counter() - started) * 1000, status_code)
