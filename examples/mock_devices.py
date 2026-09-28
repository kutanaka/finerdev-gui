"""Dummy devices for exercising devgui without real hardware.

See docs/design.md section 14, step 2:
- open/close/set/get, with configurable delay, random exceptions, and
  open failure.
- a device with no open() at all.
- a device where open()/close() are defined only on a parent class.
"""

from __future__ import annotations

import random
import threading
import time


class MockDevice:
    """A fully-featured dummy device with open/close/set/get.

    - `delay`: seconds slept inside every call, to simulate a slow
      instrument (bus worker / polling tests).
    - `fail_open`: if True, open() always raises.
    - `error_rate`: probability (0.0-1.0) that set()/get()/close() raise a
      simulated error, independent of `fail_open`.
    - `calls`: (method, start, end) monotonic-clock log of every call,
      for asserting serialization/ordering in tests.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        delay: float = 0.0,
        fail_open: bool = False,
        error_rate: float = 0.0,
        seed: int | None = None,
    ) -> None:
        self.name = name
        self.delay = delay
        self.fail_open = fail_open
        self.error_rate = error_rate
        self._rng = random.Random(seed)
        self.is_open = False
        self.value: float = 0.0
        self.calls: list[tuple[str, float, float]] = []
        self._calls_lock = threading.Lock()

    def _record(self, method: str, start: float, end: float) -> None:
        with self._calls_lock:
            self.calls.append((method, start, end))

    def _maybe_fail(self, method: str) -> None:
        if self._rng.random() < self.error_rate:
            raise RuntimeError(f"{self.name}: simulated failure in {method}()")

    def open(self) -> None:
        start = time.monotonic()
        time.sleep(self.delay)
        if self.fail_open:
            self._record("open", start, time.monotonic())
            raise RuntimeError(f"{self.name}: failed to open")
        self.is_open = True
        self._record("open", start, time.monotonic())

    def close(self) -> None:
        start = time.monotonic()
        time.sleep(self.delay)
        self._maybe_fail("close")
        self.is_open = False
        self._record("close", start, time.monotonic())

    def set(self, value: float) -> None:
        start = time.monotonic()
        time.sleep(self.delay)
        self._maybe_fail("set")
        self.value = value
        self._record("set", start, time.monotonic())

    def get(self) -> float:
        start = time.monotonic()
        time.sleep(self.delay)
        self._maybe_fail("get")
        self._record("get", start, time.monotonic())
        return self.value


class NoOpenMockDevice:
    """A dummy device with no open()/close() at all.

    Per design.md section 6.1, a device with no open() is always treated
    as `connected`.
    """

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.value: float = 0.0

    def set(self, value: float) -> None:
        self.value = value

    def get(self) -> float:
        return self.value


class _OpenableBase:
    """Defines open()/close(); used only as a parent class.

    Exercises design.md section 6.2: devgui detects open()/close() with
    plain `hasattr`/`callable`, so a subclass that inherits them (without
    redefining them itself) must be picked up with no special MRO
    handling on devgui's part.
    """

    def __init__(self, name: str = "mock") -> None:
        self.name = name
        self.is_open = False

    def open(self) -> None:
        self.is_open = True

    def close(self) -> None:
        self.is_open = False


class ChildOnlyMockDevice(_OpenableBase):
    """open()/close() come from `_OpenableBase`; only set/get are its own."""

    def __init__(self, name: str = "mock") -> None:
        super().__init__(name)
        self.value: float = 0.0

    def set(self, value: float) -> None:
        self.value = value

    def get(self) -> float:
        return self.value
