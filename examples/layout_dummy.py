"""The real lab layout (examples/layout_example.py) with dummy device I/O.

Every device instance is still built by finerdev's real, unmodified
constructor with the real arguments from docs/device_list.txt - so a
wrong class name, a wrong keyword, or a missing argument fails here
exactly as it would in the lab. Only the methods that talk to hardware
(open/close/dev and every method a widget calls) are replaced, on the
finerdev classes themselves, by in-memory dummies *before* layout_example
constructs anything. That ordering matters: loatt/synth/mp call open()
(and loatt also dev()/get()) from inside __init__, and the widgets bind
`dev.setV` etc. at layout time, so both must already see the dummies.

Each dummy must match the real method's name and signature exactly
(checked in `_install`), so the widgets' calls and the command log look
the same as with real hardware, and a finerdev API change is caught at
load time instead of silently diverging.

This file requires the `finerdev` package and FINER_LOGDIR (read by
finerdev at import time), but no network access to any instrument.
The dummies never write under FINER_LOGDIR (unlike the real loatt.set).

Run with:
    python -m devgui --layout examples/layout_dummy.py
"""

import inspect

from finerdev.loatt import loatt
from finerdev.multiplier import mp
from finerdev.sourcemeter import SourceMeter2400, SourceMeter2450
from finerdev.synth import synth


class _Connection:
    """open()/close() for every device: EtherScpi's are real sockets."""

    def open(self):
        self.isOpen = True
        return True

    def close(self):
        self.isOpen = False
        return True


class _SourceMeterDummy(_Connection):
    # get() is left real: it only returns self.val, with no I/O.

    def setV(self, volt):
        self.dummy_volt = volt
        return True

    def output(self, onOff):
        self.dummy_output = bool(onOff)
        return True


class _LoattDummy(_Connection):
    def dev(self, devid=0):
        if devid > 0:
            self.devid = devid
        return True, ""

    def get(self):
        self.att = getattr(self, "att", 0)
        return self.att

    def set(self, att):
        self.att = att
        return True


class _SynthDummy(_Connection):
    def output(self, onoff=None):
        if onoff is None:
            return int(getattr(self, "onoff", 0))
        self.onoff = bool(onoff)
        return True

    def freq(self, freqGHz=None):
        if freqGHz is None:
            return getattr(self, "freqGHz", 0.0)
        self.freqGHz = freqGHz
        return True

    def amp(self, amp=None):
        if amp is None:
            return getattr(self, "ampDBm", 0.0)
        self.ampDBm = amp
        return True


class _MpDummy(_Connection):
    def get(self):
        return True, "1" if getattr(self, "dummy_on", False) else "0"

    def on(self):
        self.dummy_on = True
        return True

    def off(self):
        self.dummy_on = False
        return True


def _install(cls, dummy):
    """Replace `cls`'s methods with `dummy`'s same-named ones, after
    checking each exists on `cls` with an identical signature."""
    for klass in reversed(dummy.__mro__[:-1]):
        for name, fn in vars(klass).items():
            if not inspect.isfunction(fn):
                continue
            real = getattr(cls, name, None)
            if real is None:
                raise TypeError(f"{cls.__name__} has no method {name}() to replace")
            if inspect.signature(real) != inspect.signature(fn):
                raise TypeError(
                    f"{cls.__name__}.{name}{inspect.signature(real)} does not match "
                    f"dummy {name}{inspect.signature(fn)}"
                )
            setattr(cls, name, fn)


_install(SourceMeter2400, _SourceMeterDummy)
_install(SourceMeter2450, _SourceMeterDummy)
_install(loatt, _LoattDummy)
_install(synth, _SynthDummy)
_install(mp, _MpDummy)

from layout_example import layout  # noqa: E402  (must follow _install)

__all__ = ["layout"]
