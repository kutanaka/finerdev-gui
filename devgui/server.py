"""FastAPI application: REST + WebSocket, including the operator right.

Per docs/design.md section 14 steps 8-9. /api/call and /api/devices/
{name}/open|close require a valid X-Operator-Token (section 7.2).
/api/operator/acquire and /release implement sections 9.1-9.3: exactly
one operator right for the whole server, freed manually, on idle
timeout, or when its holder's WebSocket stays disconnected past the
grace period. /api/operator/request(/cancel|/respond) implement the
forced-takeover state machine (section 9.4) on top of the same
OperatorManager.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import secrets
import socket
import threading
from collections import deque
from concurrent.futures import Future
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import (
    Body,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.staticfiles import StaticFiles

from devgui.ivplot import render_iv_pdf
from devgui.runtime.bus import BusManager, Priority
from devgui.runtime.devices import DeviceManager, DeviceNotInstalledError, DeviceRuntime
from devgui.runtime.operator import (
    InvalidOperatorTokenError,
    NotExternallyHeldError,
    OperatorBlockedError,
    OperatorHeldError,
    OperatorManager,
    RequestRejected,
    TakeoverCooldownError,
    TakeoverInProgressError,
    TakeoverNotFoundError,
    TransferResult,
)
from devgui.runtime.poller import Poller
from devgui.widgets import (
    Button,
    Category,
    Device,
    DigitInput,
    Display,
    NumberInput,
    Select,
    SourceMeasure,
    TextInput,
    Toggle,
    Widget,
)

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

COMMAND_LOG_MAX_ROWS = 200


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


class _RevalidateStaticFiles(StaticFiles):
    """Starlette's StaticFiles sends no Cache-Control, so browsers may
    heuristically cache index.html/app.js/style.css and skip revalidation
    entirely for a while - a real problem here, since a devgui upgrade's
    CSS/JS fix would then silently not take effect until a hard reload.
    `no-cache` forces revalidation on every load (via the ETag/
    Last-Modified Starlette already sets), which is cheap - a 304 with no
    body - and guarantees an updated file is always picked up."""

    def file_response(self, *args: Any, **kwargs: Any):
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache"
        return response


def _serialize_widget(widget: Widget, widget_states: dict[str, Any]) -> dict[str, Any]:
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
            has_get=widget.get is not None,
        )
    elif isinstance(widget, DigitInput):
        base.update(digits=widget.digits, min=widget.min, max=widget.max, default=widget.default)
    elif isinstance(widget, Toggle):
        base["default"] = widget.default
    elif isinstance(widget, Select):
        base["options"] = list(widget.options.keys())
    elif isinstance(widget, SourceMeasure):
        base.update(
            min=widget.min,
            max=widget.max,
            step=widget.step,
            sweep_default=list(widget.sweep_default),
            voltage_unit=widget.voltage_unit,
            current_unit=widget.current_unit,
            state=widget_states.get(widget.id),
        )
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


def _serialize_device(
    device: Device, runtime: DeviceRuntime, widget_states: dict[str, Any]
) -> dict[str, Any]:
    return {
        "name": device.name,
        "bus": device.bus,
        "state": runtime.state.value,
        "error_message": runtime.error_message,
        "can_open": runtime.has_open(),
        "can_close": runtime.has_close(),
        "widgets": [_serialize_widget(w, widget_states) for w in device.widgets],
    }


def _serialize_layout(
    layout: list[Category],
    device_manager: DeviceManager,
    title: str,
    widget_states: dict[str, Any],
) -> dict[str, Any]:
    return {
        "title": title,
        "categories": [
            {
                "title": category.title,
                "devices": [
                    _serialize_device(device, device_manager.get(device.name), widget_states)
                    for device in category.devices
                ],
            }
            for category in layout
        ]
    }


def _invoke_widget(widget: Widget, raw_value: Any) -> tuple[Any, tuple[Any, ...], str]:
    """Returns (result, args, method_name): `args` are the actual positional
    arguments passed to whichever callable was actually invoked, and
    `method_name` is that callable's `__name__` - used by the command log
    (section 8.2) to show the real, coerced call rather than the raw
    request body. A plain Toggle always invokes `widget.call`; a Toggle
    with `off_call` set invokes whichever of `call`/`off_call` matches the
    new switch position (section 4.3), so the log shows the real method
    that ran (e.g. "on"/"off") instead of a wrapping lambda's name."""
    if isinstance(widget, Button):
        return widget.call(), (), getattr(widget.call, "__name__", "call")
    if isinstance(widget, NumberInput):
        value = widget.type(raw_value)
        if widget.min is not None and value < widget.min:
            raise ValueError(f"value {value} is below min {widget.min}")
        if widget.max is not None and value > widget.max:
            raise ValueError(f"value {value} is above max {widget.max}")
        return widget.call(value), (value,), getattr(widget.call, "__name__", "call")
    if isinstance(widget, DigitInput):
        value = int(raw_value)
        if widget.min is not None and value < widget.min:
            raise ValueError(f"value {value} is below min {widget.min}")
        if widget.max is not None and value > widget.max:
            raise ValueError(f"value {value} is above max {widget.max}")
        return widget.call(value), (value,), getattr(widget.call, "__name__", "call")
    if isinstance(widget, Toggle):
        value = bool(raw_value)
        if widget.off_call is not None:
            fn = widget.call if value else widget.off_call
            return fn(), (), getattr(fn, "__name__", "call")
        return widget.call(value), (value,), getattr(widget.call, "__name__", "call")
    if isinstance(widget, Select):
        if raw_value not in widget.options:
            raise ValueError(f"unknown option {raw_value!r}")
        mapped = widget.options[raw_value]
        return widget.call(mapped), (mapped,), getattr(widget.call, "__name__", "call")
    if isinstance(widget, TextInput):
        value = str(raw_value)
        return widget.call(value), (value,), getattr(widget.call, "__name__", "call")
    if isinstance(widget, Display):
        return widget.call(), (), getattr(widget.call, "__name__", "call")
    raise TypeError(f"unsupported widget type {type(widget).__name__}")


class DeviceCallFailed(Exception):
    """A device method signalled failure by returning exactly `False`
    (finerdev's convention) rather than raising."""


def _iv_rows(raw: Any) -> list[list[float]]:
    """SourceMeasure's get() result -> [[current, voltage], ...]. Accepts
    one row ([I, V, ...]) or several (rows of that), as a list or a numpy
    array; columns past the first two are dropped (section 4.3)."""
    data = raw.tolist() if hasattr(raw, "tolist") else raw
    if not isinstance(data, (list, tuple)) or not data:
        raise ValueError(f"get() returned {raw!r}; expected [current, voltage, ...]")
    if not isinstance(data[0], (list, tuple)):
        data = [data]
    rows = []
    for row in data:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError(f"get() row {row!r} has fewer than 2 elements [current, voltage]")
        current, voltage = float(row[0]), float(row[1])
        if not (math.isfinite(current) and math.isfinite(voltage)):
            raise ValueError(f"get() row {row!r} is not finite")
        rows.append([current, voltage])
    return rows


def _short_repr(value: Any) -> str:
    """repr() for the command log, collapsing a multi-row sweep result
    (which could be hundreds of lines) to its shape."""
    data = value.tolist() if hasattr(value, "tolist") else value
    if isinstance(data, list) and len(data) > 3 and isinstance(data[0], list):
        return f"<{len(data)} rows x {len(data[0])}>"
    return repr(value)


def _check_voltage(widget: SourceMeasure, name: str, raw: Any) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"{name} {raw!r} is not a number")
    if widget.min is not None and value < widget.min:
        raise ValueError(f"{name} {value} is below min {widget.min}")
    if widget.max is not None and value > widget.max:
        raise ValueError(f"{name} {value} is above max {widget.max}")
    return value


def _scaled(value: float, factor: float) -> float:
    """value * factor, rounded to 12 significant digits so a unit
    conversion doesn't leave float noise (e.g. 0.30000000000000004) in
    the device call, the command log or the display."""
    return float(f"{value * factor:.12g}")


def _run_source_measure(
    widget: SourceMeasure, action: str, raw_value: Any, steps: list[dict[str, Any]]
) -> dict[str, Any]:
    """Runs one SourceMeasure action on the bus thread. Every device method
    actually called is appended to `steps` as it happens - including a
    failing one - so the caller can log each real call (section 8.2) even
    when a later step raises. Returns the state fields to update."""

    def step(fn: Any, *args: Any, check: bool = True) -> Any:
        record: dict[str, Any] = {"name": getattr(fn, "__name__", "call"), "args": args}
        steps.append(record)
        try:
            result = fn(*args)
        except Exception as exc:
            record["exc"] = exc
            raise
        record["result"] = result
        if check and result is False:
            record["failed"] = True
            reason = step(widget.message, check=False) if widget.message is not None else None
            detail = f": {reason}" if reason else ""
            raise DeviceCallFailed(f"{record['name']}() returned False{detail}")
        return result

    # Values from/to the client are in widget.voltage_unit/current_unit;
    # device methods take and return V/A.
    to_volts = widget.voltage_scale

    def to_display(rows: list[list[float]]) -> list[list[float]]:
        return [
            [_scaled(i, 1 / widget.current_scale), _scaled(v, 1 / widget.voltage_scale)]
            for i, v in rows
        ]

    if action == "output":
        value = bool(raw_value)
        step(widget.output, value)
        return {"output": value}
    if action == "meas":
        volt = _check_voltage(widget, "voltage", raw_value)
        step(widget.call, _scaled(volt, to_volts))
        step(widget.meas)
        current, voltage = to_display(_iv_rows(step(widget.get, check=False)))[-1]
        return {"current": current, "voltage": voltage}
    if action == "sweep":
        if not isinstance(raw_value, dict):
            raise ValueError("sweep needs {vstart, vend, vstep}")
        vstart = _check_voltage(widget, "vstart", raw_value.get("vstart"))
        vend = _check_voltage(widget, "vend", raw_value.get("vend"))
        vstep = float(raw_value.get("vstep"))
        if not (math.isfinite(vstep) and vstep > 0):
            raise ValueError(f"vstep {raw_value.get('vstep')!r} must be > 0")
        if vend < vstart:
            raise ValueError(f"vend {vend} is below vstart {vstart}")
        step(
            widget.sweep,
            _scaled(vstart, to_volts),
            _scaled(vend, to_volts),
            _scaled(vstep, to_volts),
        )
        rows = to_display(_iv_rows(step(widget.get, check=False)))
        current, voltage = rows[-1]
        return {
            "current": current,
            "voltage": voltage,
            "sweep": {"rows": rows, "t": _now_iso(), "params": [vstart, vend, vstep]},
        }
    raise ValueError(f"unknown SourceMeasure action {action!r}")


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
    operator_takeover_wait: float = 10.0,
    operator_takeover_cooldown: float = 30.0,
    title: str = "devgui",
    priority_hosts: tuple[str, ...] = ("127.0.0.1", "::1"),
) -> FastAPI:
    widgets_by_id: dict[str, tuple[Device, Widget]] = {
        widget.id: (device, widget)
        for category in layout
        for device in category.devices
        for widget in device.widgets
    }
    display_ids = [wid for wid, (_, w) in widgets_by_id.items() if isinstance(w, Display)]
    # Server-side, so every client (and a reload) sees the same output
    # switch, last reading and last sweep - and so meas/sweep can be
    # refused while the output is off regardless of what a client shows.
    source_measure_states: dict[str, dict[str, Any]] = {
        wid: {"output": False, "voltage": None, "current": None, "sweep": None, "running": None}
        for wid, (_, w) in widgets_by_id.items()
        if isinstance(w, SourceMeasure)
    }

    connections: set[WebSocket] = set()
    connections_by_client_id: dict[str, WebSocket] = {}
    connections_lock = asyncio.Lock()
    loop_holder: dict[str, asyncio.AbstractEventLoop] = {}
    hostname_cache: dict[str, str | None] = {}
    client_display_names: dict[str, str] = {}
    command_log: deque[dict[str, Any]] = deque(maxlen=COMMAND_LOG_MAX_ROWS)
    operator = OperatorManager(
        idle_timeout=operator_idle_timeout,
        disconnect_grace=operator_disconnect_grace,
        takeover_wait=operator_takeover_wait,
        takeover_cooldown=operator_takeover_cooldown,
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

    def _log_command(device_name: str, text: str, *, error: bool = False) -> None:
        """Append one line to the global, all-tabs command log (section
        8.2) and push it to already-connected clients; newly connecting
        clients get the buffered history via the `snapshot` message."""
        entry = {"t": _now_iso(), "device": device_name, "text": text, "error": error}
        command_log.append(entry)
        loop = loop_holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(_broadcast({"type": "command_log", **entry}), loop)

    def _log_call_result(device_name: str, call_text: str, future: Future) -> None:
        exc = future.exception()
        if exc is not None:
            _log_command(device_name, f"{call_text}  # {type(exc).__name__}: {exc}", error=True)
        else:
            _log_command(device_name, f"{call_text} -> {future.result()!r}")

    def _track_close(name: str, future: Future) -> None:
        future.add_done_callback(lambda f: _broadcast_device_state(name))
        future.add_done_callback(lambda f: _log_call_result(name, f"{name}.close()", f))

    # Record instance construction up front (section 8.2). layout.py builds
    # each `instance` itself before Device() ever sees it, so this is the
    # closest devgui can get to "when" - there is no hook into the user's
    # own constructor call. `instance_repr`, if layout.py provided one,
    # shows the real constructor arguments (e.g. the addresses from
    # device_list.txt); otherwise fall back to a generic placeholder. No
    # clients are connected yet at this point, so `_log_command` just seeds
    # the ring buffer for the first `snapshot`.
    for category in layout:
        for device in category.devices:
            if device.instance is not None:
                ctor_text = device.instance_repr or f"{type(device.instance).__name__}(...)"
                _log_command(device.name, f"{device.name} = {ctor_text}")

    def _broadcast_widget_value(widget_id: str, value: Any) -> None:
        loop = loop_holder.get("loop")
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(
            _broadcast({"type": "widget_value", "widget_id": widget_id, "value": value}), loop
        )

    def _coerce_get_result(widget: Widget, raw: Any) -> Any:
        if isinstance(widget, DigitInput):
            return int(raw)
        if isinstance(widget, NumberInput):
            return widget.type(raw)
        raise TypeError(f"unsupported gettable widget type {type(widget).__name__}")

    def _refresh_gettable_widget(device: Device, widget: Widget) -> None:
        """Read `widget.get()` once and use the result as the new displayed/
        confirmed value - mutating `widget.default` in place means a fresh
        /api/layout fetch (a reload, or a client connecting later) also
        picks it up, not just already-open clients (which get the
        `widget_value` broadcast below instead)."""
        getter = getattr(widget, "get", None)
        if getter is None:
            return
        method_name = getattr(getter, "__name__", "get")
        call_text = f"{device.name}.{method_name}()"

        def on_done(f: Future) -> None:
            if f.exception() is not None:
                logger.warning("failed to read value for %s: %s", widget.id, f.exception())
                _log_call_result(device.name, call_text, f)
                return
            try:
                value = _coerce_get_result(widget, f.result())
            except (TypeError, ValueError):
                logger.warning("%s: get() returned an incompatible value", widget.id)
                _log_command(device.name, f"{call_text} -> {f.result()!r}  # incompatible value", error=True)
                return
            widget.default = value
            _broadcast_widget_value(widget.id, value)
            _log_command(device.name, f"{call_text} -> {value!r}")

        bus_manager.submit(device.bus, getter, priority=Priority.LOW).add_done_callback(on_done)

    def _read_gettable_widgets_for_device(device: Device) -> None:
        """Once a device is connected, refresh every widget that declared a
        `get` (DigitInput, or NumberInput with get=...; section 4.3)."""
        for widget in device.widgets:
            if isinstance(widget, (DigitInput, NumberInput)) and widget.get is not None:
                _refresh_gettable_widget(device, widget)

    def _reset_source_measure_outputs(device: Device) -> None:
        """A source meter's open() resets the instrument (*RST), which
        turns its output off - mirror that instead of showing a stale ON."""
        for widget in device.widgets:
            state = source_measure_states.get(widget.id)
            if state is not None and state["output"]:
                state["output"] = False
                _broadcast_widget_value(widget.id, dict(state))

    def _track_open(device: Device, future: Future) -> None:
        def on_done(f: Future) -> None:
            _broadcast_device_state(device.name)
            _log_call_result(device.name, f"{device.name}.open()", f)
            if f.exception() is None:
                _reset_source_measure_outputs(device)
                _read_gettable_widgets_for_device(device)

        future.add_done_callback(on_done)

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

    async def _send_to_client(client_id: str | None, message: dict[str, Any]) -> None:
        if client_id is None:
            return
        ws = connections_by_client_id.get(client_id)
        if ws is None:
            return
        try:
            await ws.send_json(message)
        except Exception:
            logger.debug("failed to send a targeted message to %s", client_id, exc_info=True)

    async def _apply_transfer(result: TransferResult) -> None:
        """A takeover request resolved in the requester's favor - accepted,
        timed out, or the old holder released while it was pending."""
        await _send_to_client(
            result.old_holder_client_id,
            {
                "type": "operator_revoked",
                "reason": f"操作権が{result.requester_display_name}に移りました",
            },
        )
        await _send_to_client(
            result.requester_client_id,
            {
                "type": "operator_granted",
                "token": result.new_token,
                "display_name": result.requester_display_name,
            },
        )
        await _send_to_client(
            result.requester_client_id, {"type": "takeover_result", "result": "granted"}
        )
        await _broadcast({"type": "operator_state", **operator.snapshot()})

    async def _expiry_loop() -> None:
        while True:
            await asyncio.sleep(1.0)
            if operator.check_expiration():
                _broadcast_operator_state()
            transfer = operator.check_pending_expiration()
            if transfer is not None:
                await _apply_transfer(transfer)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loop_holder["loop"] = asyncio.get_running_loop()
        for name, future in device_manager.auto_open_all():
            _track_open(device_manager.get(name).device, future)
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
        return _serialize_layout(layout, device_manager, title, source_measure_states)

    @app.post("/api/call/{widget_id}")
    async def api_call(
        widget_id: str,
        body: dict[str, Any] = Body(default={}),
        x_operator_token: str | None = Header(default=None),
        x_client_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
        entry = widgets_by_id.get(widget_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"unknown widget '{widget_id}'")
        device, widget = entry
        raw_value = body.get("value")
        who = client_display_names.get(x_client_id, x_client_id or "unknown")
        if isinstance(widget, SourceMeasure):
            return await _call_source_measure(device, widget, body.get("action"), raw_value, who)
        # Approximate: for a plain widget this is exactly the method that
        # runs; for a dual-call Toggle it's only the "on" side, since which
        # of call/off_call actually gets invoked isn't known until inside
        # _invoke_widget - good enough for the rare failure-log case below,
        # while the success path (the common case) uses the exact name
        # _invoke_widget reports back.
        fallback_method_name = getattr(widget.call, "__name__", "call")

        def thunk() -> tuple[Any, tuple[Any, ...], str]:
            return _invoke_widget(widget, raw_value)

        future = bus_manager.submit(device.bus, thunk, priority=Priority.HIGH)
        try:
            result, call_args, method_name = await asyncio.wrap_future(future)
        except Exception as exc:
            logger.exception("call %s by %s value=%r failed", widget_id, who, raw_value)
            _log_command(
                device.name,
                f"{device.name}.{fallback_method_name}({raw_value!r})  # {type(exc).__name__}: {exc}",
                error=True,
            )
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        logger.info("call %s by %s value=%r -> ok", widget_id, who, raw_value)
        args_text = ", ".join(repr(a) for a in call_args)
        _log_command(device.name, f"{device.name}.{method_name}({args_text}) -> {result!r}")
        if isinstance(widget, (DigitInput, NumberInput)) and widget.get is not None:
            # Re-read rather than trust the value we just sent, in case the
            # device clamped/rounded it (section 4.3).
            _refresh_gettable_widget(device, widget)
        if isinstance(widget, Display):
            return {"ok": True, "result": result}
        return {"ok": True}

    async def _call_source_measure(
        device: Device, widget: SourceMeasure, action: Any, raw_value: Any, who: str
    ) -> dict[str, Any]:
        state = source_measure_states[widget.id]
        if state["running"]:
            return {"ok": False, "error": f"{action}: {state['running']} in progress"}
        if action in ("meas", "sweep") and not state["output"]:
            return {"ok": False, "error": f"{action}: output is off"}
        steps: list[dict[str, Any]] = []
        # A real sweep takes ~10 s: tell every client it's running (they
        # show "sweep in progress" and disable the panel) until it ends.
        state["running"] = action
        _broadcast_widget_value(widget.id, dict(state))
        future = bus_manager.submit(
            device.bus,
            lambda: _run_source_measure(widget, action, raw_value, steps),
            priority=Priority.HIGH,
        )
        try:
            update = await asyncio.wrap_future(future)
            error = None
        except Exception as exc:
            update = None
            error = exc
        finally:
            state["running"] = None
        for record in steps:
            args_text = ", ".join(repr(a) for a in record["args"])
            call_text = f"{device.name}.{record['name']}({args_text})"
            if "exc" in record:
                exc = record["exc"]
                _log_command(device.name, f"{call_text}  # {type(exc).__name__}: {exc}", error=True)
            elif "result" in record:
                _log_command(
                    device.name,
                    f"{call_text} -> {_short_repr(record['result'])}",
                    error=record.get("failed", False),
                )
        if error is not None:
            if not isinstance(error, DeviceCallFailed) and (not steps or "exc" not in steps[-1]):
                # Rejected before/after any device call (validation, or an
                # unusable get() result) - log that too, not just the calls.
                _log_command(
                    device.name,
                    f"{device.name} {action}({raw_value!r})  # {type(error).__name__}: {error}",
                    error=True,
                )
            logger.warning("%s %s by %s value=%r failed: %s", widget.id, action, who, raw_value, error)
            _broadcast_widget_value(widget.id, dict(state))
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        logger.info("%s %s by %s value=%r -> ok", widget.id, action, who, raw_value)
        state.update(update)
        _broadcast_widget_value(widget.id, dict(state))
        return {"ok": True, "state": dict(state)}

    @app.get("/api/widgets/{widget_id}/iv.pdf")
    async def api_iv_pdf(widget_id: str) -> Response:
        state = source_measure_states.get(widget_id)
        if state is None:
            raise HTTPException(status_code=404, detail=f"unknown SourceMeasure '{widget_id}'")
        sweep = state["sweep"]
        if sweep is None:
            raise HTTPException(status_code=404, detail="no sweep data yet")
        device, widget = widgets_by_id[widget_id]
        stamp = sweep["t"][:19]
        vstart, vend, vstep = sweep["params"]
        vu = widget.voltage_unit
        pdf = render_iv_pdf(
            sweep["rows"],
            f"{device.name}  I-V sweep",
            f"{stamp.replace('T', ' ')}   vstart={vstart:g} {vu}, vend={vend:g} {vu}, vstep={vstep:g} {vu}",
            voltage_unit=vu,
            current_unit=widget.current_unit,
        )
        filename = f"{device.name}_IV_{stamp.replace(':', '').replace('-', '')}.pdf"
        return Response(
            pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
        )

    @app.post("/api/devices/{name}/open")
    async def api_device_open(
        name: str,
        x_operator_token: str | None = Header(default=None),
        x_client_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
        who = client_display_names.get(x_client_id, x_client_id or "unknown")
        try:
            runtime = device_manager.get(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown device '{name}'")
        try:
            future = runtime.open()
        except (DeviceNotInstalledError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _broadcast_device_state(name)
        _track_open(runtime.device, future)
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:
            logger.exception("open %s by %s failed", name, who)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        logger.info("open %s by %s -> ok", name, who)
        return {"ok": True}

    @app.post("/api/devices/{name}/close")
    async def api_device_close(
        name: str,
        x_operator_token: str | None = Header(default=None),
        x_client_id: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_operator(x_operator_token)
        who = client_display_names.get(x_client_id, x_client_id or "unknown")
        try:
            runtime = device_manager.get(name)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown device '{name}'")
        try:
            future = runtime.close()
        except (DeviceNotInstalledError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        _broadcast_device_state(name)
        _track_close(name, future)
        try:
            await asyncio.wrap_future(future)
        except Exception as exc:
            logger.exception("close %s by %s failed", name, who)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        logger.info("close %s by %s -> ok", name, who)
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
        except OperatorBlockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc))
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
            result = operator.release(x_operator_token)
        except InvalidOperatorTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        if isinstance(result, TransferResult):
            # A takeover request was pending: section 9.4 item 5 hands the
            # right straight to the requester instead of freeing it.
            await _apply_transfer(result)
        else:
            _broadcast_operator_state()
        return {"ok": True}

    @app.post("/api/operator/request")
    async def api_operator_request(
        request: Request, x_client_id: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if x_client_id is None:
            raise HTTPException(status_code=400, detail="X-Client-Id header is required")
        display_name = client_display_names.get(x_client_id, x_client_id)

        if not operator.is_held:
            # Section 9.4 item 1: free right -> ordinary immediate acquire.
            token = operator.acquire(x_client_id, display_name)
            _broadcast_operator_state()
            return {
                "ok": True,
                "granted_immediately": True,
                "token": token,
                "display_name": display_name,
            }

        ip = request.client.host if request.client else "unknown"
        try:
            pending = operator.request_takeover(x_client_id, ip, display_name)
        except OperatorBlockedError as exc:
            raise HTTPException(status_code=423, detail=str(exc))
        except TakeoverInProgressError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except TakeoverCooldownError as exc:
            raise HTTPException(status_code=429, detail=str(exc))

        await _send_to_client(
            operator.client_id,
            {
                "type": "takeover_request",
                "request_id": pending.request_id,
                "requester_display_name": display_name,
                "wait_seconds": operator_takeover_wait,
            },
        )
        _broadcast_operator_state()
        return {
            "ok": True,
            "granted_immediately": False,
            "request_id": pending.request_id,
            "wait_seconds": operator_takeover_wait,
        }

    @app.post("/api/operator/request/{request_id}/cancel")
    async def api_operator_request_cancel(
        request_id: str, x_client_id: str | None = Header(default=None)
    ) -> dict[str, Any]:
        if x_client_id is None:
            raise HTTPException(status_code=400, detail="X-Client-Id header is required")
        try:
            operator.cancel_request(request_id, x_client_id)
        except TakeoverNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        # No dedicated "cancelled" WS message type exists (section 7.3); the
        # holder's dialog closes itself when operator_state reports
        # request_pending: false.
        _broadcast_operator_state()
        return {"ok": True}

    @app.post("/api/operator/request/{request_id}/respond")
    async def api_operator_request_respond(
        request_id: str,
        body: dict[str, Any] = Body(default={}),
        x_operator_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        if x_operator_token is None:
            raise HTTPException(status_code=403, detail="X-Operator-Token header is required")
        accept = bool(body.get("accept"))
        try:
            result = operator.respond_to_request(request_id, x_operator_token, accept)
        except InvalidOperatorTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except TakeoverNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

        if isinstance(result, TransferResult):
            await _apply_transfer(result)
        else:
            assert isinstance(result, RequestRejected)
            await _send_to_client(
                result.requester_client_id, {"type": "takeover_result", "result": "rejected"}
            )
            _broadcast_operator_state()
        return {"ok": True}

    # --- external control (section 9.5): devgui.priority.priority() ---

    def _require_priority_host(request: Request) -> None:
        host = request.client.host if request.client else None
        if host not in priority_hosts:
            raise HTTPException(status_code=403, detail=f"priority API not allowed from {host}")

    @app.post("/api/priority/get")
    async def api_priority_get(request: Request, body: dict[str, Any] = Body(default={})) -> dict[str, Any]:
        _require_priority_host(request)
        name = str(body.get("name") or "外部プログラム")
        block = bool(body.get("block"))
        force = bool(body.get("force"))
        try:
            result = operator.external_acquire(name, block=block, force=force)
        except OperatorHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        logger.info("priority get by %s (block=%s, force=%s) -> ok", name, block, force)
        await _send_to_client(
            result.old_holder_client_id,
            {"type": "operator_revoked", "reason": f"操作権が{name}に強制取得されました"},
        )
        if result.dropped_request is not None:
            await _send_to_client(
                result.dropped_request.requester_client_id,
                {"type": "takeover_result", "result": "rejected"},
            )
        await _broadcast({"type": "operator_state", **operator.snapshot()})
        return {"ok": True, "token": result.token, "blocked": block}

    @app.post("/api/priority/release")
    async def api_priority_release(
        request: Request, body: dict[str, Any] = Body(default={})
    ) -> dict[str, Any]:
        _require_priority_host(request)
        if body.get("force"):
            forced = operator.force_release()
            logger.info("priority release (force) -> ok")
            await _send_to_client(
                forced.old_holder_client_id,
                {"type": "operator_revoked", "reason": "操作権が外部から強制解放されました"},
            )
            if forced.dropped_request is not None:
                await _send_to_client(
                    forced.dropped_request.requester_client_id,
                    {"type": "takeover_result", "result": "rejected"},
                )
            _broadcast_operator_state()
            return {"ok": True}
        try:
            result = operator.external_release(body.get("token"))
        except NotExternallyHeldError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except InvalidOperatorTokenError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        logger.info("priority release -> ok")
        if isinstance(result, TransferResult):
            await _apply_transfer(result)
        else:
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
            connections_by_client_id[client_id] = websocket
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
                    "command_log": list(command_log),
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
                connections_by_client_id.pop(client_id, None)
            client_display_names.pop(client_id, None)
            operator.mark_disconnected(client_id)
            if operator.cancel_if_requester(client_id):
                # Section 9.4 item 3: the requester disconnecting withdraws
                # their own pending request; the holder's dialog closes via
                # the request_pending: false in this broadcast.
                _broadcast_operator_state()

    # Registered last: falls back to serving devgui/static/* (index.html at
    # "/") for anything not matched by the API/WebSocket routes above.
    app.mount("/", _RevalidateStaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
