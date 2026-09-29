"""Configuration values (design.md section 12).

`layout.py` may define a module-level `settings = Settings(...)`; CLI
arguments (devgui/__main__.py) take priority over it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Settings:
    host: str = "0.0.0.0"
    port: int = 8000
    title: str = "devgui"
    operator_idle_timeout: float = 600.0
    operator_disconnect_grace: float = 30.0
    takeover_wait: float = 10.0
    takeover_cooldown: float = 30.0
    slow_call_warning: float = 30.0
    # Client addresses allowed to use /api/priority/* (external force-
    # acquire/block, section 9.5). Loopback only by default: a program on
    # the devgui machine itself, not anyone on the network.
    priority_hosts: tuple[str, ...] = field(default=("127.0.0.1", "::1"))
