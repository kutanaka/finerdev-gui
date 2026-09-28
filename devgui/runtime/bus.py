"""Per-bus worker threads that serialize device I/O.

Per docs/design.md section 5: every device method call (open/close, a
widget's `call`, or a poll) is submitted to the queue of the device's
`bus` and executed one at a time by that bus's single worker thread, so
communication on a shared bus (e.g. a GPIB adapter) is never interleaved.

This module only implements the generic priority-queue worker mechanism.
The asyncio side awaits the returned `concurrent.futures.Future` via
`asyncio.wrap_future` (not done here, to keep this module free of an
asyncio dependency). The "skip a poll if the previous one hasn't
finished" rule (section 5.2) is the poller's responsibility (step 7),
not the bus's.
"""

from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from concurrent.futures import Future
from enum import IntEnum
from typing import Callable

logger = logging.getLogger(__name__)

_SHUTDOWN = object()


class Priority(IntEnum):
    """Lower value = served first. User actions and open/close outrank polling."""

    HIGH = 0
    LOW = 10


class Bus:
    """One worker thread and one priority queue for a single bus name."""

    def __init__(self, name: str, *, slow_call_warning: float = 30.0) -> None:
        self.name = name
        self.slow_call_warning = slow_call_warning
        self._queue: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._thread = threading.Thread(
            target=self._run, name=f"devgui-bus-{name}", daemon=True
        )
        self._thread.start()

    def submit(self, fn: Callable[[], object], *, priority: Priority = Priority.LOW) -> Future:
        future: Future = Future()
        seq = next(self._seq)
        self._queue.put((int(priority), seq, fn, future))
        return future

    def _run(self) -> None:
        while True:
            priority, seq, fn, future = self._queue.get()
            if fn is _SHUTDOWN:
                self._queue.task_done()
                break
            if future.set_running_or_notify_cancel():
                start = time.monotonic()
                try:
                    result = fn()
                except BaseException as exc:  # noqa: BLE001 - propagate via the future
                    future.set_exception(exc)
                else:
                    future.set_result(result)
                elapsed = time.monotonic() - start
                if elapsed > self.slow_call_warning:
                    logger.warning(
                        "bus %s: call took %.1fs (> %.1fs)",
                        self.name,
                        elapsed,
                        self.slow_call_warning,
                    )
            self._queue.task_done()

    def stop(self, *, wait: bool = True) -> None:
        if wait:
            self._queue.join()
        seq = next(self._seq)
        self._queue.put((int(Priority.HIGH), seq, _SHUTDOWN, Future()))
        self._thread.join()


class BusManager:
    """Creates/reuses one `Bus` per distinct bus name."""

    def __init__(self, *, slow_call_warning: float = 30.0) -> None:
        self._slow_call_warning = slow_call_warning
        self._buses: dict[str, Bus] = {}
        self._lock = threading.Lock()

    def get_bus(self, name: str) -> Bus:
        with self._lock:
            bus = self._buses.get(name)
            if bus is None:
                bus = Bus(name, slow_call_warning=self._slow_call_warning)
                self._buses[name] = bus
            return bus

    def submit(
        self, bus_name: str, fn: Callable[[], object], *, priority: Priority = Priority.LOW
    ) -> Future:
        return self.get_bus(bus_name).submit(fn, priority=priority)

    def stop_all(self, *, wait: bool = True) -> None:
        with self._lock:
            buses = list(self._buses.values())
        for bus in buses:
            bus.stop(wait=wait)
