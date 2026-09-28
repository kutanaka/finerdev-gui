"""Loading and cross-cutting validation of layout.py.

Per-object validation (call callable, poll > 0, etc.) lives in
devgui/widgets.py and raises plain ValueError/TypeError from inside
__post_init__ while layout.py is executing, so Python's own traceback
already points at the offending line. This module only adds the checks
that require the fully assembled `layout` list, and wraps file/import
failures in a single exception type for callers to catch.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from devgui.settings import Settings
from devgui.widgets import Category


class LayoutError(Exception):
    """Base for all layout.py loading/validation problems."""


class LayoutLoadError(LayoutError):
    """File missing, import failure, or missing/mistyped top-level `layout` variable."""


class LayoutValidationError(LayoutError):
    """A structural problem discovered only once the whole layout list is assembled."""


def load_layout(path: str | Path) -> list[Category]:
    layout, _settings = load_layout_and_settings(path)
    return layout


def load_layout_and_settings(path: str | Path) -> tuple[list[Category], Settings]:
    """Like `load_layout`, but also returns the optional `settings =
    Settings(...)` layout.py may define (section 4.1/12), defaulting to
    `Settings()` if absent. Imports layout.py exactly once - a second,
    separate import could re-run side effects (e.g. it may construct real
    device instances at module scope)."""
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise LayoutLoadError(f"layout.py: file not found: {resolved}")

    parent = str(resolved.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)

    spec = importlib.util.spec_from_file_location(resolved.stem, resolved)
    if spec is None or spec.loader is None:
        raise LayoutLoadError(f"layout.py: could not create import spec for {resolved}")

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except (LayoutError, ValueError, TypeError):
        raise
    except Exception as exc:
        raise LayoutLoadError(f"layout.py: failed to load {resolved}: {exc}") from exc

    layout = getattr(module, "layout", None)
    if layout is None:
        raise LayoutLoadError(
            "layout.py: no top-level 'layout' variable found (expected a list[Category])"
        )
    if not isinstance(layout, list):
        raise LayoutLoadError(
            f"layout.py: 'layout' must be a list, got {type(layout).__name__}"
        )

    for i, item in enumerate(layout):
        if not isinstance(item, Category):
            raise LayoutValidationError(
                f"layout.py: layout[{i}] is not a Category (got {type(item).__name__})"
            )

    seen: dict[str, int] = {}
    for cat_index, category in enumerate(layout):
        for device in category.devices:
            if device.name in seen:
                other_index = seen[device.name]
                other_title = layout[other_index].title
                error = LayoutValidationError(
                    f"layout.py: duplicate device name '{device.name}' "
                    f"(Category[{other_index}] '{other_title}' and "
                    f"Category[{cat_index}] '{category.title}')"
                )
                error.device_name = device.name  # type: ignore[attr-defined]
                error.category_index = cat_index  # type: ignore[attr-defined]
                raise error
            seen[device.name] = cat_index

    settings = getattr(module, "settings", None)
    if settings is None:
        settings = Settings()
    elif not isinstance(settings, Settings):
        raise LayoutLoadError(
            f"layout.py: 'settings' must be a devgui.Settings instance, "
            f"got {type(settings).__name__}"
        )

    return layout, settings
