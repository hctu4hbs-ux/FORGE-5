"""Bounded thread execution with coordinator-driven lease heartbeats."""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
import time
from typing import Any, Callable, Mapping

from .model import Claim, positive
from .store import Store

Handler = Callable[[dict[str, Any], dict[str, Any]], Any]


class Worker:
    def __init__(self, store: Store, handlers: Mapping[str, Handler], *,
                 concurrency: int = 4, lease_seconds: float = 30, poll_interval: float = 0.05):
        if type(concurrency) is not int or not 1 <= concurrency <= 256:
            raise ValueError("concurrency must be in 1..256")
        positive(lease_seconds, "lease_seconds")
        positive(poll_interval, "poll_interval")
        if not all(isinstance(key, str) and callable(handler) for key, handler in handlers.items()):
            raise ValueError("handlers must map names to callables")
        self.store, self.handlers = store, dict(handlers)
        self.concurrency, self.lease_seconds = concurrency, lease_seconds
        self.interval = min(poll_interval, lease_seconds / 3)

    def _invoke(self, claim: Claim) -> Any:
        handler = self.handlers.get(claim.handler)
        if handler is None:
            raise ValueError(f"unknown handler: {claim.handler}")
        return handler(claim.payload, claim.dependencies)

    def run(self, workflow_id: str | None = None) -> dict[str, int]:
        """Drain work, waiting for retry deadlines and other workers' live leases.

        Counts describe accepted commits by this worker. Handlers must terminate;
        Python threads cannot safely cancel arbitrary user code.
        """
        self.store.unfinished(workflow_id)
        counts = {"succeeded": 0, "failed_attempts": 0, "lease_lost": 0}
        active: dict[Future[Any], Claim] = {}
        with ThreadPoolExecutor(max_workers=self.concurrency, thread_name_prefix="durableflow") as pool:
            while True:
                for future, claim in list(active.items()):
                    if future.done():
                        try:
                            result = future.result()
                            accepted = self.store.succeed(claim, result)
                            label = "succeeded"
                        except Exception as exc:
                            accepted = self.store.fail(claim, f"{type(exc).__name__}: {exc}")
                            label = "failed_attempts"
                        counts[label if accepted else "lease_lost"] += 1
                        del active[future]
                    else:
                        # A false renewal cannot resurrect an expired or superseded lease.
                        self.store.renew(claim, lease_seconds=self.lease_seconds)
                while len(active) < self.concurrency:
                    claim = self.store.claim(lease_seconds=self.lease_seconds, workflow_id=workflow_id)
                    if claim is None:
                        break
                    active[pool.submit(self._invoke, claim)] = claim
                if not active and not self.store.unfinished(workflow_id):
                    return counts
                if active:
                    wait(active, timeout=self.interval, return_when=FIRST_COMPLETED)
                else:
                    time.sleep(self.interval)
