"""I-V sweep plot as a PDF, for SourceMeasure's "PDF" download.

Written by hand against the PDF 1.4 object model rather than with
matplotlib/reportlab: it's a single small line plot, and devgui otherwise
has no plotting or PDF dependency to pull into the lab machines. The
browser draws the same plot as SVG (app.js); the tick rule here mirrors
that code so the two look alike.
"""

from __future__ import annotations

import math
from typing import Sequence

PAGE_W, PAGE_H = 576.0, 432.0  # 8 x 6 inch, landscape
PLOT_LEFT, PLOT_RIGHT, PLOT_BOTTOM, PLOT_TOP = 84.0, 548.0, 60.0, 372.0

def nice_ticks(lo: float, hi: float, target: int = 5) -> list[float]:
    """Round-number ticks (1/2/5 x 10^k) spanning [lo, hi]: the first tick
    is <= lo and the last >= hi, so they also serve as the axis range."""
    if not (math.isfinite(lo) and math.isfinite(hi)):
        raise ValueError("non-finite axis range")
    if hi < lo:
        lo, hi = hi, lo
    if hi == lo:
        pad = abs(lo) * 0.1 or 1.0
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
    first = math.floor(lo / step + 1e-9)
    last = math.ceil(hi / step - 1e-9)
    return [round(i * step, 15) for i in range(first, last + 1)]


def unit_label(unit: str) -> str:
    """"uA" -> "µA" (the ASCII spelling used in layout.py)."""
    return "µ" + unit[1:] if unit.startswith("u") else unit


def format_tick(value: float) -> str:
    text = f"{value:.6g}"
    return "0" if text in ("-0", "0") else text


def _pdf_text(text: str) -> bytes:
    encoded = text.encode("cp1252", errors="replace")
    return b"(" + encoded.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _text_width(text: str, size: float) -> float:
    # Helvetica's digits are all 556/1000 em; letters average close to it.
    narrow = {".": 278, " ": 278, ",": 278, "-": 333, "[": 278, "]": 278, "(": 333, ")": 333}
    return sum(narrow.get(c, 556) for c in text) * size / 1000


def _text(x: float, y: float, text: str, size: float, align: str = "left") -> bytes:
    if align == "center":
        x -= _text_width(text, size) / 2
    elif align == "right":
        x -= _text_width(text, size)
    return b"BT /F1 %.1f Tf %.2f %.2f Td %s Tj ET\n" % (size, x, y, _pdf_text(text))


def render_iv_pdf(
    rows: Sequence[Sequence[float]],
    title: str,
    subtitle: str = "",
    *,
    voltage_unit: str = "V",
    current_unit: str = "A",
) -> bytes:
    """`rows` are [current, voltage] pairs (SourceMeasure's order), already
    in `current_unit`/`voltage_unit`; the plot puts voltage on x and
    current on y."""
    if not rows:
        raise ValueError("no data to plot")
    volts = [float(r[1]) for r in rows]
    amps = [float(r[0]) for r in rows]
    xticks = nice_ticks(min(volts), max(volts))
    yticks = nice_ticks(min(amps), max(amps))

    def px(v: float) -> float:
        return PLOT_LEFT + (v - xticks[0]) / (xticks[-1] - xticks[0]) * (PLOT_RIGHT - PLOT_LEFT)

    def py(i: float) -> float:
        return PLOT_BOTTOM + (i - yticks[0]) / (yticks[-1] - yticks[0]) * (PLOT_TOP - PLOT_BOTTOM)

    c = bytearray()
    c += b"0.85 G 0.5 w\n"
    for t in xticks[1:-1]:
        c += b"%.2f %.2f m %.2f %.2f l S\n" % (px(t), PLOT_BOTTOM, px(t), PLOT_TOP)
    for t in yticks[1:-1]:
        c += b"%.2f %.2f m %.2f %.2f l S\n" % (PLOT_LEFT, py(t), PLOT_RIGHT, py(t))
    c += b"0 G 1 w\n"
    c += b"%.2f %.2f %.2f %.2f re S\n" % (
        PLOT_LEFT, PLOT_BOTTOM, PLOT_RIGHT - PLOT_LEFT, PLOT_TOP - PLOT_BOTTOM
    )

    c += b"0 g\n"
    for t in xticks:
        c += b"%.2f %.2f m %.2f %.2f l S\n" % (px(t), PLOT_BOTTOM, px(t), PLOT_BOTTOM + 5)
        c += _text(px(t), PLOT_BOTTOM - 14, format_tick(t), 9, "center")
    for t in yticks:
        c += b"%.2f %.2f m %.2f %.2f l S\n" % (PLOT_LEFT, py(t), PLOT_LEFT + 5, py(t))
        c += _text(PLOT_LEFT - 5, py(t) - 3, format_tick(t), 9, "right")
    xlabel = f"Voltage [{unit_label(voltage_unit)}]"
    c += _text((PLOT_LEFT + PLOT_RIGHT) / 2, PLOT_BOTTOM - 34, xlabel, 11, "center")
    ylabel = f"Current [{unit_label(current_unit)}]"
    c += b"BT /F1 11 Tf 0 1 -1 0 %.2f %.2f Tm %s Tj ET\n" % (
        PLOT_LEFT - 50, (PLOT_BOTTOM + PLOT_TOP) / 2 - _text_width(ylabel, 11) / 2, _pdf_text(ylabel)
    )
    c += _text(PLOT_LEFT, PAGE_H - 30, title, 13)
    if subtitle:
        c += _text(PLOT_LEFT, PAGE_H - 46, subtitle, 9)

    points = [(px(v), py(i)) for v, i in zip(volts, amps)]
    c += b"0.15 0.39 0.92 RG 0.15 0.39 0.92 rg 1.2 w 1 j\n"
    c += b"%.2f %.2f m\n" % points[0]
    for x, y in points[1:]:
        c += b"%.2f %.2f l\n" % (x, y)
    c += b"S\n"
    for x, y in points:
        c += b"%.2f %.2f 3 3 re f\n" % (x - 1.5, y - 1.5)

    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>" % (PAGE_W, PAGE_H),
        b"<< /Length %d >>\nstream\n" % len(c) + bytes(c) + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)
