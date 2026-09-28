from devgui import Category, Device, Display, NumberInput

layout = [
    Category(
        "Power",
        [
            Device(
                "PSU1",
                object(),
                widgets=[
                    NumberInput("Voltage", call=lambda v: None, unit="V"),
                    Display("Readback", call=lambda: 0.0, unit="V"),
                ],
            ),
        ],
    ),
]
