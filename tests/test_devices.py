import pytest

from devgui.widgets import Button, Category, Device, Display


def test_bus_defaults_to_name():
    d = Device("PSU1", object(), widgets=[])
    assert d.bus == "PSU1"


def test_explicit_bus_is_preserved():
    d = Device("PSU1", object(), bus="gpib0", widgets=[])
    assert d.bus == "gpib0"


def test_widget_ids_are_assigned_in_order():
    d = Device(
        "PSU1",
        object(),
        widgets=[
            Button("A", call=lambda: None),
            Button("B", call=lambda: None),
        ],
    )
    assert d.widgets[0].id == "PSU1:0"
    assert d.widgets[1].id == "PSU1:1"


def test_device_allows_none_instance_for_placeholder():
    d = Device("LO att3", None, widgets=[])
    assert d.instance is None


def test_device_rejects_non_widget_items():
    with pytest.raises(TypeError):
        Device("PSU1", object(), widgets=["not a widget"])


def test_device_instance_repr_defaults_to_none():
    d = Device("PSU1", object(), widgets=[])
    assert d.instance_repr is None


def test_device_instance_repr_is_preserved():
    d = Device("LO att1", object(), widgets=[], instance_repr="loatt(devid=8)")
    assert d.instance_repr == "loatt(devid=8)"


def test_device_instance_repr_must_be_a_string():
    with pytest.raises(TypeError):
        Device("PSU1", object(), widgets=[], instance_repr=123)


def test_category_rejects_non_device_items():
    with pytest.raises(TypeError):
        Category("Power", ["not a device"])


def test_category_accepts_devices():
    d = Device("PSU1", object(), widgets=[])
    c = Category("Power", [d])
    assert c.devices == [d]


def test_display_reachable_via_widgets_list():
    d = Device("DMM1", object(), widgets=[Display("V", call=lambda: 1.0)])
    assert d.widgets[0].id == "DMM1:0"
