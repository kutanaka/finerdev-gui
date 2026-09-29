"""examples/layout_dummy.py: the real lab layout, real finerdev
constructors, dummy device I/O. Skipped where finerdev isn't installed
(it isn't a devgui dependency)."""

import os
from pathlib import Path

import pytest

if "FINER_LOGDIR" not in os.environ:
    pytest.skip("finerdev needs FINER_LOGDIR at import time", allow_module_level=True)
pytest.importorskip("finerdev.sourcemeter")

from finerdev.loatt import loatt  # noqa: E402
from finerdev.multiplier import mp  # noqa: E402
from finerdev.sourcemeter import SourceMeter2400, SourceMeter2450  # noqa: E402
from finerdev.synth import synth  # noqa: E402

from devgui.layout_loader import load_layout  # noqa: E402

LAYOUT = Path(__file__).parent.parent / "examples" / "layout_dummy.py"

# name -> (real class, constructor-set attributes from docs/device_list.txt)
EXPECTED = {
    "SourceMeter1": (SourceMeter2400, {"devid": 25, "ipAddr": "prologix"}),
    "SourceMeter2": (SourceMeter2400, {"devid": 18, "ipAddr": "prologix"}),
    "SourceMeter3": (SourceMeter2450, {"ipAddr": "finer-sm3", "port": 5025}),
    "SourceMeter4": (SourceMeter2450, {"ipAddr": "finer-sm4", "port": 5025}),
    "LO att1": (loatt, {"devid": 8, "ipAddr": "prologix"}),
    "LO att2": (loatt, {"devid": 9, "ipAddr": "prologix"}),
    "synth (B4+5)": (synth, {"ipAddr": "finer-sg45", "port": 5025}),
    "synth (B6+7)": (synth, {"ipAddr": "finer-sg67", "port": 5025}),
    "multiplier": (mp, {"ipAddr": "finer-mp", "port": 5025}),
}


@pytest.fixture(scope="module")
def devices():
    layout = load_layout(LAYOUT)
    return {d.name: d for c in layout for d in c.devices}


def test_every_instance_built_by_real_constructor(devices):
    for name, (cls, attrs) in EXPECTED.items():
        instance = devices[name].instance
        assert type(instance) is cls, name
        # __init__ is never patched: it's still finerdev's own.
        assert cls.__init__.__module__.startswith("finerdev."), name
        for attr, value in attrs.items():
            assert getattr(instance, attr) == value, (name, attr)
    assert devices["LO att3"].instance is None
    assert devices["LO att4"].instance is None


def test_widget_calls_hit_dummies_not_hardware(devices):
    sm = devices["SourceMeter3"].instance
    assert sm.open() is True and sm.isOpen
    assert sm.setV(0.001) is True and sm.dummy_volt == 0.001
    assert sm.meas() is True
    assert sm.get()[1] == 0.001  # [current, voltage, ...]
    assert sm.measIV(0.0, 0.005, 0.001) is True
    assert sm.get().shape == (6, 5)

    att = devices["LO att1"].instance
    assert att.set(123) is True and att.get() == 123

    sg = devices["synth (B4+5)"].instance
    sg.freq(12.5)
    sg.amp(-3.0)
    sg.output(True)
    assert (sg.freq(), sg.amp(), sg.output()) == (12.5, -3.0, 1)

    m = devices["multiplier"].instance
    m.on()
    assert m.get() == (True, "1")
    m.off()
    assert m.get() == (True, "0")
