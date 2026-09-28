"""Device lifecycle and state management.

Per docs/design.md section 6: each device has a state; open()/close() are
detected purely with hasattr()/callable() (no MRO-walking); a device with
no open() is always `connected`, except a `Device(instance=None)`
placeholder, which is always `not_installed` (the one documented
exception, added alongside that feature). All open()/close() calls go
through the bus queue (section 5) so they serialize with everything else
on that device's bus.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future
from enum import Enum

from devgui.runtime.bus import BusManager, Priority
from devgui.widgets import Category, Device

logger = logging.getLogger(__name__)


class DeviceState(Enum):
    NOT_INSTALLED = "not_installed"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


class DeviceNotInstalledError(Exception):
    """Raised when open()/close() is attempted on a not_installed placeholder."""


class DeviceRuntime:
    """Live state for one `Device`, backed by its bus's worker thread."""

    def __init__(self, device: Device, bus_manager: BusManager) -> None:
        self.device = device
        self._bus_manager = bus_manager
        self._lock = threading.Lock()
        self.error_message: str | None = None

        if device.instance is None:
            self._state = DeviceState.NOT_INSTALLED
        elif self.has_open():
            self._state = DeviceState.DISCONNECTED
        else:
            self._state = DeviceState.CONNECTED

    def has_open(self) -> bool:
        return callable(getattr(self.device.instance, "open", None))

    def has_close(self) -> bool:
        return callable(getattr(self.device.instance, "close", None))

    @property
    def state(self) -> DeviceState:
        with self._lock:
            return self._state

    def _set_state(self, state: DeviceState, *, error_message: str | None = None) -> None:
        with self._lock:
            self._state = state
            self.error_message = error_message

    def open(self) -> Future:
        if self.device.instance is None:
            raise DeviceNotInstalledError(self.device.name)
        if not self.has_open():
            raise ValueError(f"Device '{self.device.name}' has no open()")

        self._set_state(DeviceState.CONNECTING)
        future = self._bus_manager.submit(
            self.device.bus, self.device.instance.open, priority=Priority.HIGH
        )

        def _on_done(f: Future) -> None:
            exc = f.exception()
            if exc is not None:
                self._set_state(DeviceState.ERROR, error_message=f"{type(exc).__name__}: {exc}")
            else:
                self._set_state(DeviceState.CONNECTED)

        future.add_done_callback(_on_done)
        return future

    def close(self) -> Future:
        if self.device.instance is None:
            raise DeviceNotInstalledError(self.device.name)
        if not self.has_close():
            raise ValueError(f"Device '{self.device.name}' has no close()")

        self._set_state(DeviceState.DISCONNECTING)
        future = self._bus_manager.submit(
            self.device.bus, self.device.instance.close, priority=Priority.HIGH
        )

        def _on_done(f: Future) -> None:
            exc = f.exception()
            if exc is not None:
                self._set_state(DeviceState.ERROR, error_message=f"{type(exc).__name__}: {exc}")
            else:
                self._set_state(DeviceState.DISCONNECTED)

        future.add_done_callback(_on_done)
        return future


class DeviceManager:
    """Owns one `DeviceRuntime` per device declared in the layout."""

    def __init__(self, layout: list[Category], bus_manager: BusManager) -> None:
        self.layout = layout
        self.bus_manager = bus_manager
        self.runtimes: dict[str, DeviceRuntime] = {
            device.name: DeviceRuntime(device, bus_manager)
            for category in layout
            for device in category.devices
        }

    def get(self, name: str) -> DeviceRuntime:
        return self.runtimes[name]

    def auto_open_all(self) -> list[tuple[str, Future]]:
        """Open every eligible device (section 6.3). One failure does not
        stop the others: each device's open() runs as its own future."""
        futures = []
        for runtime in self.runtimes.values():
            if runtime.device.auto_open and runtime.state == DeviceState.DISCONNECTED:
                futures.append((runtime.device.name, runtime.open()))
        return futures

    def close_all_connected(self) -> None:
        """Close every connected device (section 6.6). Failures are logged,
        not raised, so shutdown always proceeds."""
        pending: list[tuple[DeviceRuntime, Future]] = []
        for runtime in self.runtimes.values():
            if runtime.state == DeviceState.CONNECTED and runtime.has_close():
                pending.append((runtime, runtime.close()))

        for runtime, future in pending:
            exc = future.exception()
            if exc is not None:
                logger.error(
                    "failed to close device '%s': %s: %s",
                    runtime.device.name,
                    type(exc).__name__,
                    exc,
                )
