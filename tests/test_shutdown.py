"""Confirms design.md section 13's explicit check: on SIGTERM, section 6.6's
close() handling actually runs before the process exits."""

import signal
import subprocess
import sys
import time


def test_sigterm_triggers_close_before_exit(tmp_path):
    marker = tmp_path / "closed.marker"
    layout_source = f"""
from devgui import Category, Device


class ClosingDevice:
    def open(self):
        self.is_open = True

    def close(self):
        with open({str(marker)!r}, "w") as fp:
            fp.write("closed")


layout = [Category("A", [Device("D1", ClosingDevice(), widgets=[])])]
"""
    layout_path = tmp_path / "layout.py"
    layout_path.write_text(layout_source)

    proc = subprocess.Popen(
        [sys.executable, "-m", "devgui", "--layout", str(layout_path), "--port", "8321"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        started = False
        while time.monotonic() < deadline:
            line = proc.stdout.readline()
            if "Application startup complete" in line:
                started = True
                break
        assert started, "server did not report startup complete in time"

        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=10)

        assert marker.exists()
        assert marker.read_text() == "closed"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
