"""Two-column PDFs must be read column by column, or no quote can ever match (grounding then rejects real values).

Measured: an MM-ARC paper interleaved its columns, so 13 of 19 extracted values were stripped as QUOTE_NOT_FOUND even
though the paper states every one of them ("we evaluate 62 instruments" / "across five asset classes" were split by a
line from the other column).
"""

from __future__ import annotations

import io

import pdfplumber
from fixtures import make_pdf, make_two_column_pdf

from qsd.handlers import parse_bytes
from qsd.handlers.pdf import _column_split, page_text  # noqa: PLC2701 - testing the layout probe directly

LEFT = [
    "We evaluate 62 instruments across five",
    "asset classes with one protocol.",
    "The same cost model applies to every",
    "market in the study.",
    "The router converts state into exposure",
    "and projects it onto the feasible set.",
]
RIGHT = [
    "The sample builder extracts the most",
    "recent observations for each market.",
    "Under an all-in one-way cost of 10",
    "basis points per unit of turnover the",
    "results stay positive for every seed.",
    "That completes the robustness audit.",
]


def _flat(text: str) -> str:
    """One line, single spaces: the shape grounding compares against (it normalises whitespace itself)."""
    return " ".join(text.split())


def test_two_column_text_is_not_interleaved():
    """The whole sentence must survive contiguously, which is what grounding requires."""
    text = _flat(parse_bytes(make_two_column_pdf(LEFT, RIGHT), name="paper.pdf").text)
    assert "We evaluate 62 instruments across five asset classes with one protocol." in text
    assert "Under an all-in one-way cost of 10 basis points per unit of turnover" in text
    # the interleaved failure mode put a right-column line inside those sentences
    assert "five The sample builder" not in text
    assert "turnover The router" not in text


def test_reading_order_is_left_column_then_right():
    text = _flat(parse_bytes(make_two_column_pdf(LEFT, RIGHT), name="paper.pdf").text)
    assert text.index("We evaluate 62 instruments") < text.index("The sample builder extracts")
    # nothing from the right column appears before the left column ends
    assert text.index("feasible set") < text.index("The sample builder")


def test_a_single_column_page_is_untouched():
    """The probe must not fire on ordinary one-column text."""
    result = parse_bytes(make_pdf(["A single column paper about momentum in equity markets."]), name="one.pdf")
    assert "A single column paper about momentum in equity markets." in result.text


def test_the_layout_probe_reports_no_gutter_for_one_column():
    with pdfplumber.open(io.BytesIO(make_pdf(["x"]))) as pdf:
        assert _column_split(pdf.pages[0]) is None


def test_the_layout_probe_finds_the_gutter_for_two_columns():
    with pdfplumber.open(io.BytesIO(make_two_column_pdf(LEFT, RIGHT))) as pdf:
        gutter = _column_split(pdf.pages[0])
    assert gutter is not None and 150 < gutter < 450


def test_page_text_falls_back_when_there_is_little_text():
    """A near-empty page (figure, title page) must not be mangled by the column logic."""
    with pdfplumber.open(io.BytesIO(make_pdf(["Short."]))) as pdf:
        assert page_text(pdf.pages[0]) != ""


def test_a_word_crossing_the_gutter_makes_the_probe_decline():
    """A line genuinely wider than the column (a long URL) means there is no clean gutter, so the page is left alone.

    That is the safe outcome: splitting a page that is not cleanly two-column would reorder text and destroy more
    quotes than it fixes. Documents like this need a smarter order heuristic, which is tracked separately.
    """
    left = [
        "We hold the position while the trend",
        "See https://example.org/a/very/long/reference/that/crosses for details",
        "filters agree and the volatility regime",
        "remains below the stated threshold.",
    ]
    right = [
        "The exit rule closes the position when",
        "the trend filter flips or the stop is hit,",
        "which keeps the holding period bounded",
        "and the turnover within the stated budget.",
    ]
    import io

    import pdfplumber

    with pdfplumber.open(io.BytesIO(make_two_column_pdf(left, right))) as pdf:
        assert _column_split(pdf.pages[0]) is None
    # and the text is still returned, just in pdfplumber's own order
    result = parse_bytes(make_two_column_pdf(left, right), name="cross.pdf")
    assert "volatility regime" in _flat(result.text)
