"""Layout model: Category, Device, and the widget classes declared in layout.py.

See docs/design.md sections 4.2/4.3 for the authoritative field list and
defaults; this module implements them as-is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Sequence


@dataclass
class Widget:
    label: str
    call: Callable[..., Any]
    id: str = field(default="", init=False, repr=True)

    def __post_init__(self) -> None:
        if not callable(self.call):
            raise TypeError(
                f"{type(self).__name__} '{self.label}': call must be callable"
            )


@dataclass
class Button(Widget):
    confirm: bool = False


@dataclass
class NumberInput(Widget):
    """`get`, if given, opts this widget into the same get/set pending-value
    behavior as DigitInput (section 4.3): the entry shows in blue while it
    differs from the last-confirmed value, "設定" (and a "キャンセル" button
    next to it) are only enabled while there's a pending change, `get` seeds
    the initial value whenever the device connects, and is called again
    after every successful "設定" to reflect the device's actual resulting
    value rather than just assuming it took the sent value verbatim. With no
    `get` (the default), NumberInput behaves exactly as before: a plain
    entry with an always-enabled "設定" button and no cancel button.
    """

    unit: str | None = None
    min: float | None = None
    max: float | None = None
    step: float | None = None
    default: float | None = None
    type: type = float
    get: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(
                f"NumberInput '{self.label}': min ({self.min}) must be <= max ({self.max})"
            )
        if self.type not in (int, float):
            raise ValueError(
                f"NumberInput '{self.label}': type must be int or float, got {self.type!r}"
            )
        if self.get is not None and not callable(self.get):
            raise TypeError(f"NumberInput '{self.label}': get must be callable or None")


@dataclass
class DigitInput(Widget):
    """A decimal, fixed-width digit-wheel input (each digit independently
    incremented/decremented), for device codes like an attenuator setting
    rather than a continuous physical quantity. `call` only fires when the
    operator commits with the widget's own "設定" action - not per digit
    change. See docs/design.md section 4.3.

    `get`, if given, is called once whenever the owning device becomes
    `connected` (auto_open at startup or a later manual reconnect), and its
    result seeds the widget's displayed/confirmed value - unlike Toggle/
    Select, which never read back hardware state (section 4.3).
    """

    digits: int = 4
    min: int | None = None
    max: int | None = None
    default: int = 0
    get: Callable[[], Any] | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.digits <= 0:
            raise ValueError(f"DigitInput '{self.label}': digits must be > 0, got {self.digits}")
        if self.get is not None and not callable(self.get):
            raise TypeError(f"DigitInput '{self.label}': get must be callable or None")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(
                f"DigitInput '{self.label}': min ({self.min}) must be <= max ({self.max})"
            )


@dataclass
class Toggle(Widget):
    default: bool = False


@dataclass
class Select(Widget):
    options: Sequence[Any] | dict[str, Any] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__post_init__()
        if isinstance(self.options, dict):
            normalized: dict[str, Any] = dict(self.options)
        else:
            normalized = {}
            for value in self.options:
                key = str(value)
                if key in normalized:
                    raise ValueError(
                        f"Select '{self.label}': options collide under display key "
                        f"'{key}' ({normalized[key]!r} and {value!r})"
                    )
                normalized[key] = value
        self.options = normalized


@dataclass
class TextInput(Widget):
    default: str = ""


@dataclass
class Display(Widget):
    unit: str | None = None
    poll: float = 1.0
    fmt: str | None = None
    visible_rows: int = 5
    max_rows: int = 50

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.poll <= 0:
            raise ValueError(f"Display '{self.label}': poll must be > 0, got {self.poll}")
        if self.visible_rows > self.max_rows:
            raise ValueError(
                f"Display '{self.label}': visible_rows ({self.visible_rows}) must be "
                f"<= max_rows ({self.max_rows})"
            )
        if self.fmt is not None and not isinstance(self.fmt, str):
            raise ValueError(f"Display '{self.label}': fmt must be a string or None")


@dataclass
class Device:
    """`instance_repr`, if given, is human-readable text for how `instance`
    was actually constructed (e.g. "loatt(devid=8)"), shown in the command
    log's instance-creation line (section 8.2). layout.py builds `instance`
    itself before Device ever sees it, so devgui has no way to recover the
    real constructor call on its own - this field lets layout.py supply it
    explicitly. With no `instance_repr`, the log falls back to a generic
    "{ClassName}(...)".
    """

    name: str
    instance: Any
    bus: str | None = field(default=None, kw_only=True)
    widgets: Sequence[Widget] = field(default=(), kw_only=True)
    auto_open: bool = field(default=True, kw_only=True)
    instance_repr: str | None = field(default=None, kw_only=True)

    def __post_init__(self) -> None:
        if self.bus is None:
            self.bus = self.name

        for i, w in enumerate(self.widgets):
            if not isinstance(w, Widget):
                raise TypeError(
                    f"Device '{self.name}': widgets[{i}] is not a Widget (got {type(w).__name__})"
                )

        for i, w in enumerate(self.widgets):
            w.id = f"{self.name}:{i}"

        if self.instance_repr is not None and not isinstance(self.instance_repr, str):
            raise TypeError(f"Device '{self.name}': instance_repr must be a string or None")


@dataclass
class Category:
    title: str
    devices: Sequence[Device]

    def __post_init__(self) -> None:
        for i, d in enumerate(self.devices):
            if not isinstance(d, Device):
                raise TypeError(
                    f"Category '{self.title}': devices[{i}] is not a Device (got {type(d).__name__})"
                )
