"""FastAPI application: REST + WebSocket skeleton.

Per docs/design.md section 14 step 5, this wires up /api/layout,
/api/call/{widget_id}, and the WebSocket `hello`/`snapshot` handshake.
Operator-token enforcement (section 9) does not exist yet, so every
mutating endpoint here is provisionally open to any caller - that check
is added in a later step.

/api/devices/{name}/open and /close (section 7.2) are included here too,
since the frontend built in step 6 needs them and they follow directly
from devgui/runtime/devices.py (step 4); the operator-only *endpoints*
themselves (/api/operator/...) are still out of scope until steps 8-9.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from concurrent.futures import Future
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Body, FastAPI, HTTPException, WebSocket, WebSocketDisconnect

from devgui.runtime.bus import BusManager, Priority
from devgui.runtime.devices import DeviceManager, DeviceNotInstalledError, DeviceRuntime
from devgui.widgets import (
    Button,
    Category,
    Device,
    Display,
    NumberInput,
    Select,
    TextInput,
    Toggle,
    Widget,
)

logger = logging.getLogger(__name__)


def _serialize_widget(widget: Widget) -> dict[str, Any]:
    base: dict[str, Any] = {"id": widget.id, "type": type(widget).__name__, "label": widget.label}
    if isinstance(widget, Button):
        base["confirm"] = widget.confirm
    elif isinstance(widget, NumberInput):
        base.update(
            unit=widget.unit,
            min=widget.min,
            max=widget.max,
            step=widget.step,
            default=widget.default,
            value_type=widget.type.__name__,
        )
    elif isinstance(widget, Toggle):
        base["default"] = widget.default
    elif isinstance(widget, Select):
        base["options"] = list(widget.options.keys())
    elif isinstance(widget, TextInput):
        base["default"] = widget.default
    elif isinstance(widget, Display):
        base.update(
            unit=widget.unit,
            poll=widget.poll,
            visible_rows=widget.visible_rows,
            max_rows=widget.max_rows,
        )
    return base


def _serialize_device(device: Device, runtime: DeviceRuntime) -> dict[str, Any]:
    return {
        "name": device.name,
        "bus": device.bus,
        "state": runtime.state.value,
        "error_message": runtime.error_message,
        "can_open": runtime.has_open(),
        "can_close": runtime.has_close(),
        "widgets": [_serialize_widget(w) for w in device.widgets],
    }


def _serialize_layout(layout: list[Category], device_manager: DeviceManager) -> dict[str, Any]:
    return {
        "categories": [
            {
                "title": category.title,
                "devices": [
                    _serialize_device(device, device_manager.get(device.name))
                    for device in category.devices
                ],
            }
            for category in layout
        ]
    }


def _invoke_widget(widget: Widget, raw_value: Any) -> Any:
    if isinstance(widget, Button):
        return widget.call()
    if isinstance(widget, NumberInput):
        value = widget.type(raw_value)
        if widget.min is not None and value < widget.min:
            raise ValueError(f"value {value} is below min {widget.min}")
        if widget.max is not None and value > widget.max:
            raise ValueError(f"value {value} is above max {widget.max}")
        return widget.call(value)
    if isinstance(widget, Toggle):
        return widget.call(bool(raw_value))
    if isinstance(widget, Select):
        if raw_value not in widget.options:
            raise ValueError(f"unknown option {raw_value!r}")
        return widget.call(widget.options[raw_value])
    if isinstance(widget, TextInput):
        return widget.call(str(raw_value))
    if isinstance(widget, Display):
        return widget.call()
    raise TypeError(f"unsupported widget type {type(widget).__name__}")


def create_app(
    layout: list[Category], device_manager: DeviceManager, bus_manager: BusManager
) -> FastAPI:
    widgets_by_id: dict[str, tuple[Device, Widget]] = {
        widget.id: (device, widget)
        for category in layout
        for device in category.devices
        for widget in device.widgets
    }

    connections: set[WebSocket] = set()
    connections_lock = asyncio.Lock()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}

    async def _broadcast(message: dict[str, Any]) -> None:
        async with connections_lock:
            targets = list(connections)
        for ws in targets:
            try:
                await ws.send_json(message)
            except Exception:
                logger.debug("failed to send to a websocket client", exc_info=True)

    def _broadcast_device_state(name: str) -> None:
        runtime = device_manager.get(name)
        loop = loop_holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            _broadcast(
                {
                    "type": "device_state",
                    "name": name,
                    "state": runtime.state.value,
                    "error_message": runtime.error_message,
                }
            ),
            loop,
        )

    def _track(name: str, future: Future) -> None:
        future.add_done_callback(lambda f: _broadcast_device_state(name))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_holder["loop"] = asyncio.get_running_loop()
        for name, future in device_manager.auto_open_all():
            _track(name, future)
        yield
        await asyncio.to_thread(device_manager.close_all_connected)

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/layout")
    async def api_layout() -> dict[str, Any]:
        return _serialize_layout(layout, device_manager)

    @app.post("/api/call/{widget_id}")
    async def api_call(widget_id: str, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        entry = widgets_by_id.get(widget_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown widget '{widget_id}'")
        device, widget = entry
        raw_value = body.get("value")

        def thunk() -> Any:
            return _invoke_widget(widget, raw_value)

        future = bus_manager.submit(device.bus, thunk, priority=Priority.HIGH)
        try:
            result = await asyncio.wrap_future(future)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(widget, Display):
            return {"ok": True, "result": result}
        return {"ok": True}

    @app.post("/api/devices/{name}/open")
    async def api_device_open(name: str) -> dict[str, Any]:
        try:
            runtime = device_manager.get(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown device '{name}'")
        try:
            future = runtime.open()
        except (DeviceNotInstalledError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _broadcast_device_state(name)
        _track(name, future)
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    @app.post("/api/devices/{name}/close")
    async def api_device_close(name: str) -> dict[str, Any]:
        try:
            runtime = device_manager.get(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown device '{name}'")
        try:
            future = runtime.close()
        except (DeviceNotInstalledError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _broadcast_device_state(name)
        _track(name, future)
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        client_id = secrets.token_urlsafe(16)
        async with connections_lock:
            connections.add(websocket)
        try:
            await websocket.send_json({"type": "hello", "client_id": client_id})
            await websocket.send_json(
                {
                    "type": "snapshot",
                    "devices": {
                        name: {"state": rt.state.value, "error_message": rt.error_message}
                        for name, rt in device_manager.runtimes.items()
                    },
                }
            )
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            async with connections_lock:
                connections.discard(websocket)

    return app
