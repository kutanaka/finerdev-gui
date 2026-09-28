"""Display polling and the per-widget log ring buffer.

Per docs/design.md section 8: each Display widget has a
collections.deque(maxlen=max_rows) ring buffer; a row is
{"t": iso8601-with-ms, "value": formatted string, "error": bool}, where
`t` is the server time at which the call finished. A failing call is
recorded as an error row rather than raised.

Per section 5.2: if the previous poll for a Display hasn't finished yet,
the next tick is skipped rather than queued, so a slow device can't make
its bus queue grow without bound. Per section 6.5, polling only runs
while the owning device is `connected`.

This module has no asyncio-to-websocket knowledge of its own: `on_entry`
is a plain callback the caller (server.py) uses to bridge a new log entry
out to WebSocket clients, the same way device_state changes are bridged
in server.py.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import deque
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Callable

from devgui.runtime.bus import BusManager, Priority
from devgui.runtime.devices import DeviceManager, DeviceState
from devgui.widgets import Category, Device, Display


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class Poller:
    def __init__(
        self,
        layout: list[Category],
        device_manager: DeviceManager,
        bus_manager: BusManager,
        *,
        on_entry: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.device_manager = device_manager
        self.bus_manager = bus_manager
        self._on_entry = on_entry
        self._logs: dict[str, deque[dict[str, Any]]] = {}
        self._displays: dict[str, tuple[Device, Display]] = {}
        for category in layout:
            for device in category.devices:
                for widget in device.widgets:
                    if isinstance(widget, Display):
                        self._logs[widget.id] = deque(maxlen=widget.max_rows)
                        self._displays[widget.id] = (device, widget)
        self._tasks: list[asyncio.Task] = []

    def get_log(self, widget_id: str) -> list[dict[str, Any]]:
        return list(self._logs.get(widget_id, ()))

    def start(self) -> None:
        for widget_id, (device, widget) in self._displays.items():
            task = asyncio.create_task(self._poll_loop(widget_id, device, widget))
            self._tasks.append(task)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()

    async def _poll_loop(self, widget_id: str, device: Device, widget: Display) -> None:
        pending: Future | None = None
        while True:
            await asyncio.sleep(widget.poll)

            if pending is not None and not pending.done():
                continue  # previous poll still in flight: skip this tick

            runtime = self.device_manager.get(device.name)
            if runtime.state != DeviceState.CONNECTED:
                continue

            pending = self.bus_manager.submit(device.bus, widget.call, priority=Priority.LOW)
            pending.add_done_callback(
                lambda f, wid=widget_id, w=widget: self._handle_result(wid, w, f)
            )

    def _handle_result(self, widget_id: str, widget: Display, future: Future) -> None:
        exc = future.exception()
        if exc is not None:
            entry = {"t": _now_iso(), "value": f"{type(exc).__name__}: {exc}", "error": True}
        else:
            value = future.result()
            text = widget.fmt.format(value) if widget.fmt else str(value)
            entry = {"t": _now_iso(), "value": text, "error": False}

        self._logs[widget_id].append(entry)
        if self._on_entry is not None:
            self._on_entry(widget_id, entry)
