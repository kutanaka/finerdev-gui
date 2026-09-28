"""FastAPI application: REST + WebSocket, including the operator right.

Per docs/design.md section 14 step 8, /api/call and /api/devices/{name}/
open|close now require a valid X-Operator-Token (section 7.2); the
forced-takeover endpoints (/api/operator/request...) are still out of
scope until step 9. /api/operator/acquire and /release implement
sections 9.1-9.3: exactly one operator right for the whole server, freed
manually, on idle timeout, or when its holder's WebSocket stays
disconnected past the grace period - not on any device-specific state.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import socket
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from devgui.runtime.bus import BusManager, Priority
from devgui.runtime.devices import DeviceManager, DeviceNotInstalledError, DeviceRuntime
from devgui.runtime.operator import InvalidOperatorTokenError, OperatorHeldError, OperatorManager
from devgui.runtime.poller import Poller
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

STATIC_DIR = Path(__file__).parent / "static"


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


def _lookup_hostname_background(ip: str, cache: dict[str, str | None]) -> None:
    # A real daemon thread, not a ThreadPoolExecutor/asyncio.to_thread worker:
    # socket.gethostbyaddr() has no way to time out, and both the default
    # executor and the interpreter itself join non-daemon threads at
    # shutdown, which would hang the whole process behind one slow/dead
    # reverse-DNS lookup. A daemon thread is simply abandoned on exit.
    def worker() -> None:
        try:
            cache[ip] = socket.gethostbyaddr(ip)[0]
        except Exception:
            pass  # cache[ip] stays None (set as a placeholder before spawning)

    threading.Thread(target=worker, daemon=True, name=f"devgui-dns-{ip}").start()


def _display_name_for(ip: str, cache: dict[str, str | None]) -> str:
    """Best-effort "IP, or hostname (IP) once resolved" - never blocks.

    The first call for a given IP returns just the IP and kicks off a
    background resolution; later calls (e.g. a reconnect) benefit from
    whatever the cache holds by then. Per design.md section 7.1: the
    reverse lookup is cached and happens asynchronously with a timeout -
    here "timeout" means callers are never blocked waiting for it.
    """
    if ip not in cache:
        cache[ip] = None
        _lookup_hostname_background(ip, cache)
        return ip
    hostname = cache[ip]
    return f"{hostname} ({ip})" if hostname else ip


def create_app(
    layout: list[Category],
    device_manager: DeviceManager,
    bus_manager: BusManager,
    *,
    operator_idle_timeout: float = 600.0,
    operator_disconnect_grace: float = 30.0,
) -> FastAPI:
    widgets_by_id: dict[str, tuple[Device, Widget]] = {
        widget.id: (device, widget)
        for category in layout
        for device in category.devices
        for widget in device.widgets
    }
    display_ids = [wid for wid, (_, w) in widgets_by_id.items() if isinstance(w, Display)]

    connections: set[WebSocket] = set()
    connections_lock = asyncio.Lock()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}
    hostname_cache: dict[str, str | None] = {}
    client_display_names: dict[str, str] = {}
    operator = OperatorManager(
        idle_timeout=operator_idle_timeout, disconnect_grace=operator_disconnect_grace
    )
    background_tasks: list[asyncio.Task] = []

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

    def _on_display_entry(widget_id: str, entry: dict[str, Any]) -> None:
        loop = loop_holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "display_entry", "widget_id": widget_id, **entry}), loop
        )

    poller = Poller(layout, device_manager, bus_manager, on_entry=_on_display_entry)

    def _broadcast_operator_state() -> None:
        loop = loop_holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "operator_state", **operator.snapshot()}), loop
        )

    def _require_operator(token: str | None) -> None:
        if not operator.is_holder(token):
            raise HTTPException(status_code=403, detail="operator right required")
        operator.touch(token)

    async def _expiry_loop() -> None:
        while True:
            await asyncio.sleep(1.0)
            if operator.check_expiration():
                _broadcast_operator_state()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_holder["loop"] = asyncio.get_running_loop()
        for name, future in device_manager.auto_open_all():
            _track(name, future)
        poller.start()
        background_tasks.append(asyncio.create_task(_expiry_loop()))
        yield
        for task in background_tasks:
            task.cancel()
        for task in background_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await poller.stop()
        await asyncio.to_thread(device_manager.close_all_connected)

    app = FastAPI(lifespan=lifespan)

    @app.get("/api/layout")
    async def api_layout() -> dict[str, Any]:
        return _serialize_layout(layout, device_manager)

    @app.post("/api/call/{widget_id}")
    async def api_call(
        widget_id: str,
        body: dict[str, Any] = Body(default={}),
        x_operator_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
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
    async def api_device_open(
        name: str, x_operator_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
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
    async def api_device_close(
        name: str, x_operator_token: str | None = Header(default=None)
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
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

    @app.post("/api/operator/acquire")
    async def api_operator_acquire(
        x_client_id: str | None = Header(default=None),
        x_operator_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_client_id is None:
            raise HTTPException(status_code=400, detail="X-Client-Id header is required")
        display_name = client_display_names.get(x_client_id, x_client_id)
        try:
            token = operator.acquire(
                x_client_id, display_name, presented_token=x_operator_token
            )
        except OperatorHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _broadcast_operator_state()
        return {"ok": True, "token": token, "display_name": display_name}

    @app.post("/api/operator/release")
    async def api_operator_release(
        x_operator_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_operator_token is None:
            raise HTTPException(status_code=403, detail="X-Operator-Token header is required")
        try:
            operator.release(x_operator_token)
        except InvalidOperatorTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        _broadcast_operator_state()
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        client_id = secrets.token_urlsafe(16)
        client_ip = websocket.client.host if websocket.client else "unknown"
        client_display_names[client_id] = _display_name_for(client_ip, hostname_cache)
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
                    "displays": {wid: poller.get_log(wid) for wid in display_ids},
                    "operator": operator.snapshot(),
                }
            )
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            async with connections_lock:
                connections.discard(websocket)
            client_display_names.pop(client_id, None)
            operator.mark_disconnected(client_id)

    # Registered last: falls back to serving devgui/static/* (index.html at
    # "/") for anything not matched by the API/WebSocket routes above.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
