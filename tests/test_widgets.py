import pytest

from devgui.widgets import Button, DigitInput, Display, NumberInput, Select, TextInput, Toggle


def test_button_defaults():
    b = Button("Reset", call=lambda: None)
    assert b.label == "Reset"
    assert b.confirm is False


def test_call_must_be_callable():
    with pytest.raises(TypeError):
        Button("Reset", call="not callable")


def test_display_poll_must_be_positive():
    with pytest.raises(ValueError):
        Display("V", call=lambda: 1.0, poll=0)
    with pytest.raises(ValueError):
        Display("V", call=lambda: 1.0, poll=-1)


def test_display_visible_rows_must_not_exceed_max_rows():
    with pytest.raises(ValueError):
        Display("V", call=lambda: 1.0, visible_rows=10, max_rows=5)


def test_display_defaults_are_valid():
    d = Display("V", call=lambda: 1.0)
    assert d.poll == 1.0
    assert d.visible_rows == 5
    assert d.max_rows == 50


def test_number_input_min_must_not_exceed_max():
    with pytest.raises(ValueError):
        NumberInput("V", call=lambda v: None, min=10, max=0)


def test_number_input_type_must_be_int_or_float():
    with pytest.raises(ValueError):
        NumberInput("V", call=lambda v: None, type=str)


def test_number_input_get_defaults_to_none():
    n = NumberInput("V", call=lambda v: None)
    assert n.get is None


def test_number_input_get_must_be_callable():
    with pytest.raises(TypeError):
        NumberInput("V", call=lambda v: None, get="not callable")


def test_number_input_defaults_are_valid():
    n = NumberInput("V", call=lambda v: None)
    assert n.type is float


def test_toggle_default():
    t = Toggle("On/Off", call=lambda v: None)
    assert t.default is False


def test_text_input_default():
    t = TextInput("Cmd", call=lambda v: None)
    assert t.default == ""


def test_select_accepts_list_and_normalizes_to_dict():
    s = Select("Range", call=lambda v: None, options=["1V", "10V"])
    assert s.options == {"1V": "1V", "10V": "10V"}


def test_select_accepts_dict_as_is():
    s = Select("Range", call=lambda v: None, options={"1 V": 1, "10 V": 10})
    assert s.options == {"1 V": 1, "10 V": 10}


def test_select_rejects_colliding_list_values():
    with pytest.raises(ValueError):
        Select("Range", call=lambda v: None, options=[1, "1"])


def test_digit_input_defaults():
    d = DigitInput("Att", call=lambda v: None)
    assert d.digits == 4
    assert d.min is None
    assert d.max is None
    assert d.default == 0
    assert d.get is None


def test_digit_input_get_must_be_callable():
    with pytest.raises(TypeError):
        DigitInput("Att", call=lambda v: None, get="not callable")


def test_digit_input_digits_must_be_positive():
    with pytest.raises(ValueError):
        DigitInput("Att", call=lambda v: None, digits=0)
    with pytest.raises(ValueError):
        DigitInput("Att", call=lambda v: None, digits=-1)


def test_digit_input_min_must_not_exceed_max():
    with pytest.raises(ValueError):
        DigitInput("Att", call=lambda v: None, min=100, max=0)
