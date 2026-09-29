import re

import pytest

from devgui.ivplot import format_tick, nice_ticks, render_iv_pdf, unit_label


def test_nice_ticks_bracket_range_with_round_steps():
    assert nice_ticks(0, 0.005) == [0, 0.001, 0.002, 0.003, 0.004, 0.005]
    ticks = nice_ticks(1.3e-6, 2.9e-5)
    assert ticks[0] <= 1.3e-6 and ticks[-1] >= 2.9e-5
    assert nice_ticks(-3, 7)[0] <= -3


def test_nice_ticks_degenerate_range_is_padded():
    ticks = nice_ticks(0.002, 0.002)
    assert ticks[0] < 0.002 < ticks[-1]
    assert len(nice_ticks(0, 0)) > 1


def test_unit_label_and_format():
    assert unit_label("uA") == "\u00b5A"
    assert unit_label("mV") == "mV"
    assert format_tick(4.0) == "4"
    assert format_tick(0.030000000000000002) == "0.03"


def test_render_iv_pdf_is_well_formed():
    rows = [[k * 1e-6, k * 0.001] for k in range(6)]
    pdf = render_iv_pdf(rows, "SM1  I-V sweep", "subtitle (x)", voltage_unit="mV", current_unit="mA")
    assert pdf.startswith(b"%PDF-1.4\n") and pdf.endswith(b"%%EOF\n")
    # every xref offset must point at its "N 0 obj"
    xref_at = int(re.search(rb"startxref\n(\d+)", pdf).group(1))
    assert pdf[xref_at:].startswith(b"xref")
    offsets = [int(m) for m in re.findall(rb"(\d{10}) 00000 n", pdf)]
    for n, off in enumerate(offsets, start=1):
        assert pdf[off:].startswith(b"%d 0 obj" % n)
    length = int(re.search(rb"/Length (\d+)", pdf).group(1))
    start = pdf.index(b"stream\n") + len(b"stream\n")
    assert pdf[start + length :].startswith(b"endstream")
    assert b"\\(x\\)" in pdf  # parentheses escaped
    assert b"(Voltage [mV])" in pdf and b"(Current [mA])" in pdf


def test_render_iv_pdf_needs_data():
    with pytest.raises(ValueError):
        render_iv_pdf([], "t")
