import pytest

from examples.mock_devices import ChildOnlyMockDevice, MockDevice, NoOpenMockDevice


def test_open_close_toggle_is_open():
    d = MockDevice()
    assert d.is_open is False
    d.open()
    assert d.is_open is True
    d.close()
    assert d.is_open is False


def test_set_get_roundtrip():
    d = MockDevice()
    d.set(3.5)
    assert d.get() == 3.5


def test_fail_open_always_raises_and_leaves_closed():
    d = MockDevice(fail_open=True)
    with pytest.raises(RuntimeError):
        d.open()
    assert d.is_open is False


def test_error_rate_zero_never_raises():
    d = MockDevice(error_rate=0.0, seed=1)
    for _ in range(50):
        d.set(1.0)
        d.get()


def test_error_rate_one_always_raises():
    d = MockDevice(error_rate=1.0, seed=1)
    with pytest.raises(RuntimeError):
        d.set(1.0)
    with pytest.raises(RuntimeError):
        d.get()


def test_delay_is_reflected_in_call_log():
    d = MockDevice(delay=0.01)
    d.set(1.0)
    method, start, end = d.calls[-1]
    assert method == "set"
    assert end - start >= 0.01


def test_calls_are_logged_in_order():
    d = MockDevice()
    d.open()
    d.set(1.0)
    d.get()
    d.close()
    assert [c[0] for c in d.calls] == ["open", "set", "get", "close"]


def test_no_open_mock_device_has_no_open_or_close():
    d = NoOpenMockDevice()
    assert not hasattr(d, "open")
    assert not hasattr(d, "close")
    d.set(2.0)
    assert d.get() == 2.0


def test_child_only_mock_device_inherits_open_close():
    d = ChildOnlyMockDevice()
    assert hasattr(d, "open") and callable(d.open)
    assert hasattr(d, "close") and callable(d.close)
    assert d.is_open is False
    d.open()
    assert d.is_open is True
    d.set(5.0)
    assert d.get() == 5.0
    d.close()
    assert d.is_open is False
