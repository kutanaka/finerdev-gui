from pathlib import Path

import pytest

from devgui.layout_loader import LayoutLoadError, LayoutValidationError, load_layout

FIXTURES = Path(__file__).parent / "fixtures" / "layouts"


def test_load_valid_minimal_layout():
    layout = load_layout(FIXTURES / "valid_minimal.py")
    assert len(layout) == 1
    assert layout[0].title == "Power"
    device = layout[0].devices[0]
    assert device.name == "PSU1"
    assert device.widgets[0].id == "PSU1:0"
    assert device.widgets[1].id == "PSU1:1"


def test_sys_path_injection_allows_sibling_import():
    layout = load_layout(FIXTURES / "valid_with_sibling_import.py")
    assert layout[0].devices[0].name == "PSU1"


def test_nonexistent_path_raises_layout_load_error():
    with pytest.raises(LayoutLoadError):
        load_layout(FIXTURES / "does_not_exist.py")


def test_missing_layout_variable(write_layout):
    path = write_layout("x = 1\n")
    with pytest.raises(LayoutLoadError):
        load_layout(path)


def test_layout_wrong_top_level_type(write_layout):
    path = write_layout("layout = {}\n")
    with pytest.raises(LayoutLoadError):
        load_layout(path)


def test_layout_element_not_a_category(write_layout):
    source = """
from devgui import Category, Device

layout = [Category("A", [Device("D1", object(), widgets=[])]), "not a category"]
"""
    path = write_layout(source)
    with pytest.raises(LayoutValidationError):
        load_layout(path)


def test_duplicate_device_name_across_categories(write_layout):
    source = """
from devgui import Category, Device

layout = [
    Category("A", [Device("PSU1", object(), widgets=[])]),
    Category("B", [Device("PSU1", object(), widgets=[])]),
]
"""
    path = write_layout(source)
    with pytest.raises(LayoutValidationError):
        load_layout(path)


def test_widget_level_error_propagates_unwrapped(write_layout):
    source = """
from devgui import Category, Device, Display

layout = [
    Category("A", [Device("D1", object(), widgets=[Display("V", call=lambda: 0, poll=-1)])]),
]
"""
    path = write_layout(source)
    with pytest.raises(ValueError):
        load_layout(path)


def test_no_duplicate_widget_ids_across_a_realistic_layout(write_layout):
    source = """
from devgui import Category, Device, Button, Display

def widgets_for(name):
    return [Button(f"{name}-btn", call=lambda: None), Display(f"{name}-disp", call=lambda: 0)]

layout = [
    Category("A", [
        Device("Dev1", object(), widgets=widgets_for("Dev1")),
        Device("Dev2", object(), widgets=widgets_for("Dev2")),
    ]),
    Category("B", [
        Device("Dev3", object(), widgets=widgets_for("Dev3")),
    ]),
]
"""
    path = write_layout(source)
    layout = load_layout(path)
    ids = [w.id for category in layout for device in category.devices for w in device.widgets]
    assert len(ids) == len(set(ids))
