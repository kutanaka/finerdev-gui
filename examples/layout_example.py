"""Real lab layout, generated from docs/device_list.txt.

See docs/finerdev_devices.md for the full device_list.txt -> instance
mapping and the known mismatches between finerdev's actual behavior and
design.md's idealized assumptions (accepted as-is per project decision).

Two modes, chosen at startup:

    python -m devgui --layout examples/layout_example.py           # real hardware
    python -m devgui --layout examples/layout_example.py --dummy   # no hardware

(or DEVGUI_DUMMY=1 instead of --dummy). Dummy mode installs
examples/finer_dummy.py's in-memory I/O on the finerdev classes before
anything is constructed; the instances themselves are still built by
finerdev's real constructors with the real addresses.

Requires the `finerdev` package in both modes. FINER_LOGDIR (read by
several finerdev modules at import time) defaults to ~/finer/log when
it isn't already set in the environment - an explicit value always wins.
Real mode also needs network access to the `prologix` GPIB-Ethernet
bridge and to finer-sm3/finer-sm4/finer-sg45/finer-sg67/finer-mp.
"""

import os
from pathlib import Path

# Must precede the finerdev imports below (they read it at import time).
os.environ.setdefault("FINER_LOGDIR", str(Path.home() / "finer" / "log"))

from devgui import (  # noqa: E402
    Category,
    Device,
    DigitInput,
    Display,
    NumberInput,
    SourceMeasure,
    Toggle,
    is_dummy,
)
from finerdev.loatt import loatt  # noqa: E402
from finerdev.multiplier import mp  # noqa: E402
from finerdev.sourcemeter import SourceMeter2400, SourceMeter2450  # noqa: E402
from finerdev.synth import synth  # noqa: E402

if is_dummy():
    import finer_dummy

    finer_dummy.install()  # before any instance below is constructed

GPIB_BUS = "gpib-prologix"  # SourceMeter1/2 and LO att1/2 share one physical
# GPIB-Ethernet bridge ("prologix"); they must serialize on the same bus.


def build(cls, *args, **kwargs):
    """Construct `cls(*args, **kwargs)` and also return a repr of exactly
    that call, for `Device(..., instance_repr=...)` (docs/design.md section
    8.2): the command log then shows the real constructor arguments - the
    addresses from docs/device_list.txt - instead of a generic placeholder.
    Building the repr from the same `args`/`kwargs` actually passed (rather
    than a hand-typed string) means it can't drift from what's really
    called."""
    instance = cls(*args, **kwargs)
    call_args = ", ".join([repr(a) for a in args] + [f"{k}={v!r}" for k, v in kwargs.items()])
    return instance, f"{cls.__name__}({call_args})"


source_meter1, source_meter1_repr = build(SourceMeter2400, devid=25, ipAddr="prologix")
source_meter2, source_meter2_repr = build(SourceMeter2400, devid=18, ipAddr="prologix")
source_meter3, source_meter3_repr = build(SourceMeter2450, ipAddr="finer-sm3")
source_meter4, source_meter4_repr = build(SourceMeter2450, ipAddr="finer-sm4")

lo_att1, lo_att1_repr = build(loatt, devid=8)
lo_att2, lo_att2_repr = build(loatt, devid=9)

synth_b45, synth_b45_repr = build(synth, addr="finer-sg45")
synth_b67, synth_b67_repr = build(synth, addr="finer-sg67")

multiplier, multiplier_repr = build(mp, addr="finer-mp")


def source_meter_widgets(dev):
    # One composite widget: "meas" is setV -> meas -> get and "sweep" is
    # measIV -> get, both only while the output is on; get() returns
    # [current, voltage, ...] (or rows of that after measIV). No polled
    # readback Display - get() only ever returns the last measurement.
    # get_message() supplies the reason when a finerdev method returns
    # False. The panel works in mV/mA; devgui converts to the V/A finerdev
    # takes and returns (2 mV typed -> setV(0.002)). max=5 mV matches
    # finerdev's own setV/measIV limit.
    return [
        SourceMeasure(
            "SourceMeter",
            call=dev.setV,
            output=dev.output,
            meas=dev.meas,
            sweep=dev.measIV,
            get=dev.get,
            message=dev.get_message,
            voltage_unit="mV",
            current_unit="mA",
            min=0,
            max=5,
            step=0.1,
            sweep_default=(0.0, 5.0, 0.1),
        ),
    ]


def loatt_widgets(dev):
    # No readback Display: the attenuation is only ever changed from this
    # panel, so a continuously-polled echo of what we just set adds no
    # information - the digit-wheel input's own pending/confirmed coloring
    # and the "設定" action's success/failure feedback cover it.
    # DigitInput (not NumberInput) since the device code is a raw 4-digit
    # value (0-4095, sent as a 4-hex-digit command internally), not a
    # continuous physical quantity. get=dev.get reads the device's actual
    # current attenuation once whenever it (re)connects, so the panel
    # starts from reality instead of always showing 0000.
    return [
        DigitInput("減衰量設定", call=dev.set, digits=4, min=0, max=4095, get=dev.get),
    ]


def synth_widgets(dev):
    # No readback Display for freq/amp either, for the same reason as
    # loatt_widgets above - these are settings this panel owns, not
    # independently-changing measurements. get=dev.freq/dev.amp reuses the
    # same dual-purpose method as call= (finerdev's freq()/amp() return the
    # current value when called with no argument), giving the same
    # get/set pending-value behavior as loatt's DigitInput, entry-style.
    return [
        NumberInput("周波数設定", call=dev.freq, get=dev.freq, unit="GHz", min=0, step=0.001),
        NumberInput("出力レベル設定", call=dev.amp, get=dev.amp, unit="dBm"),
        Toggle("出力ON/OFF", call=dev.output),
    ]


layout = [
    Category(
        "RX",
        [
            Device(
                "SourceMeter1",
                source_meter1,
                bus=GPIB_BUS,
                widgets=source_meter_widgets(source_meter1),
                instance_repr=source_meter1_repr,
            ),
            Device(
                "SourceMeter2",
                source_meter2,
                bus=GPIB_BUS,
                widgets=source_meter_widgets(source_meter2),
                instance_repr=source_meter2_repr,
            ),
            Device(
                "SourceMeter3",
                source_meter3,
                widgets=source_meter_widgets(source_meter3),
                instance_repr=source_meter3_repr,
            ),
            Device(
                "SourceMeter4",
                source_meter4,
                widgets=source_meter_widgets(source_meter4),
                instance_repr=source_meter4_repr,
            ),
        ],
    ),
    Category(
        "LO",
        [
            Device(
                "LO att1", lo_att1, bus=GPIB_BUS, widgets=loatt_widgets(lo_att1), instance_repr=lo_att1_repr
            ),
            Device(
                "LO att2", lo_att2, bus=GPIB_BUS, widgets=loatt_widgets(lo_att2), instance_repr=lo_att2_repr
            ),
            # Connection details (GPIB address) not yet decided - placeholder
            # panels only, per design.md section 4.2's `instance=None`.
            Device("LO att3", None, widgets=[]),
            Device("LO att4", None, widgets=[]),
            Device(
                "synth (B4+5)", synth_b45, widgets=synth_widgets(synth_b45), instance_repr=synth_b45_repr
            ),
            Device(
                "synth (B6+7)", synth_b67, widgets=synth_widgets(synth_b67), instance_repr=synth_b67_repr
            ),
            Device(
                "multiplier",
                multiplier,
                widgets=[
                    # `mp` exposes on()/off() as two separate no-arg methods
                    # rather than a single set(bool) - off_call lets this
                    # stay one slide-switch-style Toggle (design.md 4.3)
                    # while the command log still shows whichever real
                    # method actually ran (section 8.2).
                    Toggle("出力ON/OFF", call=multiplier.on, off_call=multiplier.off),
                    Display("状態読み出し", call=multiplier.get, poll=2.0),
                ],
                instance_repr=multiplier_repr,
            ),
        ],
    ),
]
