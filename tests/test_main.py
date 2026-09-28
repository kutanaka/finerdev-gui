import pytest

from devgui.__main__ import main, parse_args


def test_parse_args_requires_layout():
    with pytest.raises(SystemExit):
        parse_args([])


def test_parse_args_defaults():
    args = parse_args(["--layout", "layout.py"])
    assert args.layout == "layout.py"
    assert args.host is None
    assert args.port is None


def test_parse_args_host_and_port():
    args = parse_args(["--layout", "layout.py", "--host", "127.0.0.1", "--port", "9000"])
    assert args.host == "127.0.0.1"
    assert args.port == 9000


@pytest.fixture
def capture_uvicorn_run(monkeypatch):
    calls = []

    def fake_run(app, *, host, port):
        calls.append({"app": app, "host": host, "port": port})

    monkeypatch.setattr("devgui.__main__.uvicorn.run", fake_run)
    return calls


def test_main_runs_server_with_settings_from_layout(write_layout, capture_uvicorn_run):
    source = """
from devgui import Category, Device, Settings

layout = [Category("A", [Device("D1", object(), widgets=[])])]
settings = Settings(host="127.0.0.1", port=9001, title="lab")
"""
    path = write_layout(source)

    exit_code = main(["--layout", str(path)])

    assert exit_code == 0
    assert len(capture_uvicorn_run) == 1
    call = capture_uvicorn_run[0]
    assert call["host"] == "127.0.0.1"
    assert call["port"] == 9001


def test_main_cli_args_override_layout_settings(write_layout, capture_uvicorn_run):
    source = """
from devgui import Category, Settings

layout = []
settings = Settings(host="127.0.0.1", port=9001)
"""
    path = write_layout(source)

    exit_code = main(["--layout", str(path), "--host", "0.0.0.0", "--port", "8080"])

    assert exit_code == 0
    call = capture_uvicorn_run[0]
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 8080


def test_main_uses_default_settings_when_absent(write_layout, capture_uvicorn_run):
    source = """
from devgui import Category

layout = []
"""
    path = write_layout(source)

    exit_code = main(["--layout", str(path)])

    assert exit_code == 0
    call = capture_uvicorn_run[0]
    assert call["host"] == "0.0.0.0"
    assert call["port"] == 8000


def test_main_returns_1_and_prints_error_on_invalid_layout(capsys, capture_uvicorn_run):
    exit_code = main(["--layout", "/no/such/file.py"])

    assert exit_code == 1
    assert capture_uvicorn_run == []
    captured = capsys.readouterr()
    assert "devgui:" in captured.err
