"""Dummy (no hardware) vs real mode, chosen at startup.

`python -m devgui --dummy ...` or the environment variable DEVGUI_DUMMY=1
(e.g. `Environment=DEVGUI_DUMMY=1` in a systemd unit) selects dummy mode.
devgui itself only marks the UI (title suffix, top-bar color); what
"dummy" means for the devices is up to layout.py, which asks
`is_dummy()` - e.g. to swap its instruments' I/O for in-memory stand-ins
(see examples/layout_example.py). Read at layout load time, so it's
decided once per server process.
"""

from __future__ import annotations

import os

DUMMY_ENV = "DEVGUI_DUMMY"


def is_dummy() -> bool:
    return os.environ.get(DUMMY_ENV, "").strip().lower() in ("1", "true", "yes", "on")
