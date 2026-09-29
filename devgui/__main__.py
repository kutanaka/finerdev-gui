"""CLI entrypoint: python -m devgui --layout /path/to/layout.py [--host H] [--port P]

Per docs/design.md section 3. CLI arguments take priority over a
`settings = Settings(...)` declared in layout.py (section 12).
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn

from devgui.layout_loader import LayoutError, load_layout_and_settings
from devgui.runtime.bus import BusManager
from devgui.runtime.devices import DeviceManager
from devgui.server import create_app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="devgui")
    parser.add_argument("--layout", required=True, help="path to layout.py")
    parser.add_argument("--host", default=None, help="override settings.host")
    parser.add_argument("--port", type=int, default=None, help="override settings.port")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    try:
        layout, settings = load_layout_and_settings(args.layout)
    except LayoutError as exc:
        print(f"devgui: {exc}", file=sys.stderr)
        return 1

    if args.host is not None:
        settings.host = args.host
    if args.port is not None:
        settings.port = args.port

    bus_manager = BusManager(slow_call_warning=settings.slow_call_warning)
    device_manager = DeviceManager(layout, bus_manager)
    app = create_app(
        layout,
        device_manager,
        bus_manager,
        operator_idle_timeout=settings.operator_idle_timeout,
        operator_disconnect_grace=settings.operator_disconnect_grace,
        operator_takeover_wait=settings.takeover_wait,
        operator_takeover_cooldown=settings.takeover_cooldown,
        title=settings.title,
        priority_hosts=settings.priority_hosts,
    )

    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
