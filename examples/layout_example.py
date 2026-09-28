"""Real lab layout, generated from docs/device_list.txt.

See docs/finerdev_devices.md for the full device_list.txt -> instance
mapping and the known mismatches between finerdev's actual behavior and
design.md's idealized assumptions (accepted as-is per project decision).

This file requires:
- The `finerdev` package installed and importable.
- The FINER_LOGDIR environment variable set (several finerdev modules
  read it at import time and raise KeyError otherwise).
- Network access to the `prologix` GPIB-Ethernet bridge and to
  finer-sm3/finer-sm4/finer-sg45/finer-sg67/finer-mp.

It will NOT import successfully on a machine without that real lab
environment - it is not exercised by the test suite (which uses
examples/mock_devices.py instead) or by the manual mock-device
walkthrough in README.md.

Run with:
    python -m devgui --layout examples/layout_example.py
"""

from devgui import Category, Device, DigitInput, Display, NumberInput, Select, Toggle
from finerdev.loatt import loatt
from finerdev.multiplier import mp
from finerdev.sourcemeter import SoureMeter2400, SourceMeter2450
from finerdev.synth import synth

GPIB_BUS = "gpib-prologix"  # SourceMeter1/2 and LO att1/2 share one physical
# GPIB-Ethernet bridge ("prologix"); they must serialize on the same bus.

source_meter1 = SoureMeter2400(devid=25, ipAddr="prologix")
source_meter2 = SoureMeter2400(devid=18, ipAddr="prologix")
source_meter3 = SourceMeter2450(ipAddr="finer-sm3")
source_meter4 = SourceMeter2450(ipAddr="finer-sm4")

lo_att1 = loatt(devid=8)
lo_att2 = loatt(devid=9)

synth_b45 = synth(addr="finer-sg45")
synth_b67 = synth(addr="finer-sg67")

multiplier = mp(addr="finer-mp")


def source_meter_widgets(dev):
    return [
        NumberInput("電圧設定", call=dev.setV, unit="V", min=0, max=0.005, step=0.0001),
        Toggle("出力ON/OFF", call=dev.output),
        Display("読み出し", call=dev.get, poll=2.0),
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
    # independently-changing measurements.
    return [
        NumberInput("周波数設定", call=dev.freq, unit="GHz", min=0, step=0.001),
        NumberInput("出力レベル設定", call=dev.amp, unit="dBm"),
        Toggle("出力ON/OFF", call=dev.output),
    ]


layout = [
    Category(
        "RX",
        [
            Device("SourceMeter1", source_meter1, bus=GPIB_BUS, widgets=source_meter_widgets(source_meter1)),
            Device("SourceMeter2", source_meter2, bus=GPIB_BUS, widgets=source_meter_widgets(source_meter2)),
            Device("SourceMeter3", source_meter3, widgets=source_meter_widgets(source_meter3)),
            Device("SourceMeter4", source_meter4, widgets=source_meter_widgets(source_meter4)),
        ],
    ),
    Category(
        "LO",
        [
            Device("LO att1", lo_att1, bus=GPIB_BUS, widgets=loatt_widgets(lo_att1)),
            Device("LO att2", lo_att2, bus=GPIB_BUS, widgets=loatt_widgets(lo_att2)),
            # Connection details (GPIB address) not yet decided - placeholder
            # panels only, per design.md section 4.2's `instance=None`.
            Device("LO att3", None, widgets=[]),
            Device("LO att4", None, widgets=[]),
            Device("synth (B4+5)", synth_b45, widgets=synth_widgets(synth_b45)),
            Device("synth (B6+7)", synth_b67, widgets=synth_widgets(synth_b67)),
            Device(
                "multiplier",
                multiplier,
                widgets=[
                    Toggle("出力ON/OFF", call=lambda on: multiplier.on() if on else multiplier.off()),
                    Display("状態読み出し", call=multiplier.get, poll=2.0),
                ],
            ),
        ],
    ),
]
