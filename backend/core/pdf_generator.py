"""Salary slip PDF generation.

Takes a validated `SalarySlipData` record and renders a clean, professional
one-page salary slip as in-memory PDF bytes. Optionally encrypts the result
with a password (bonus feature) using pypdf's AES-256.

Design notes
------------
* Framework-agnostic and stateless: input is a model, output is `bytes`.
  Nothing touches the filesystem, so it runs fine on Render / serverless and
  the email layer can attach the bytes directly.
* fpdf2's built-in (core) fonts use Latin-1 encoding and CANNOT render the
  Unicode rupee glyph (U+20B9). We use the "Rs." label instead, which avoids
  a runtime encoding crash and reads correctly on an Indian payslip.
* Money is formatted with Indian digit grouping (1,23,45,678) and always two
  decimal places. The `net_salary` value comes straight off the model, so the
  PDF can never disagree with the canonical formula.
"""

from __future__ import annotations

import io
from decimal import ROUND_HALF_UP, Decimal

from fpdf import FPDF
from pypdf import PdfReader, PdfWriter

from core.file_parser import SalarySlipData

CURRENCY_LABEL = "Rs."
DEFAULT_COMPANY_NAME = "Nippon Toyota"


# --------------------------------------------------------------------------- #
# Formatting helpers
# --------------------------------------------------------------------------- #


def format_currency(amount: Decimal) -> str:
    """Format a Decimal as 'Rs. 1,23,45,678.00' using Indian digit grouping."""
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    sign = "-" if quantized < 0 else ""
    whole, _, frac = f"{abs(quantized):.2f}".partition(".")

    # Indian grouping: last 3 digits, then groups of 2.
    if len(whole) <= 3:
        grouped = whole
    else:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        grouped = ",".join(parts) + "," + tail

    return f"{CURRENCY_LABEL} {sign}{grouped}.{frac}"


def derive_pdf_password(slip: SalarySlipData, birth_year: int | None = None) -> str:
    """Derive the PDF password for the bonus feature.

    Convention:
        * If a birth year is known -> first 4 letters of the name (lowercase,
          no spaces) + birth year, e.g. "aara1998". Matches the assignment's
          "name + birth year" suggestion.
        * Otherwise fall back to the assignment's "unique code" option: first
          4 letters of the name + Employee ID, e.g. "aaraE001".

    Document whichever convention you ship so the email body / README can tell
    employees how to open their slip.
    """
    name_part = "".join(ch for ch in slip.name.lower() if ch.isalpha())[:4]
    suffix = str(birth_year) if birth_year is not None else slip.employee_id
    return f"{name_part}{suffix}"


# --------------------------------------------------------------------------- #
# PDF document
# --------------------------------------------------------------------------- #


class _SalarySlipPDF(FPDF):
    """fpdf2 subclass that owns the recurring header/footer chrome."""

    def __init__(self, company_name: str, period: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self._company_name = company_name
        self._period = period
        self.set_auto_page_break(auto=True, margin=18)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 10, self._company_name, align="L")
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 10, "SALARY SLIP", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_font("Helvetica", "", 10)
        self.set_text_color(90, 90, 90)
        self.cell(0, 6, f"Pay Period: {self._period}", align="R", new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(0, 0, 0)
        self.ln(2)
        self.set_draw_color(180, 180, 180)
        self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
        self.ln(6)

    def footer(self) -> None:
        self.set_y(-18)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120, 120, 120)
        self.cell(
            0, 6,
            "This is a computer-generated document and does not require a signature.",
            align="C",
        )


# --------------------------------------------------------------------------- #
# Internal section renderers
# --------------------------------------------------------------------------- #


def _render_employee_block(pdf: _SalarySlipPDF, slip: SalarySlipData) -> None:
    label_w, value_w = 35, 60
    rows = [
        ("Employee ID", slip.employee_id, "Designation", slip.designation),
        ("Name", slip.name, "Email", slip.email),
    ]
    for left_label, left_value, right_label, right_value in rows:
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 7, f"{left_label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(value_w, 7, str(left_value))
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(label_w, 7, f"{right_label}:")
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 7, str(right_value), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)


def _render_earnings_deductions(pdf: _SalarySlipPDF, slip: SalarySlipData) -> None:
    col_w = (pdf.w - pdf.l_margin - pdf.r_margin) / 2
    amount_w = 38

    # Section headers
    pdf.set_fill_color(31, 41, 55)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(col_w, 8, "  Earnings", fill=True)
    pdf.cell(col_w, 8, "  Deductions", fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    earnings = [
        ("Basic Salary", slip.base_salary),
        ("House Rent Allowance", slip.hra),
        ("Other Allowances", slip.allowances),
    ]
    deductions = [("Total Deductions", slip.deductions)]

    pdf.set_font("Helvetica", "", 10)
    max_rows = max(len(earnings), len(deductions))
    for i in range(max_rows):
        # Earnings cell (left column)
        if i < len(earnings):
            label, value = earnings[i]
            pdf.cell(col_w - amount_w, 7, f"  {label}", border="L")
            pdf.cell(amount_w, 7, format_currency(value), border="R", align="R")
        else:
            pdf.cell(col_w, 7, "", border="LR")
        # Deductions cell (right column)
        if i < len(deductions):
            label, value = deductions[i]
            pdf.cell(col_w - amount_w, 7, f"  {label}", border="L")
            pdf.cell(amount_w, 7, format_currency(value), border="R", align="R",
                     new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(col_w, 7, "", border="LR", new_x="LMARGIN", new_y="NEXT")

    # Subtotals row
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(243, 244, 246)
    pdf.cell(col_w - amount_w, 8, "  Gross Earnings", border="LTB", fill=True)
    pdf.cell(amount_w, 8, format_currency(slip.gross_salary), border="RTB", fill=True, align="R")
    pdf.cell(col_w - amount_w, 8, "  Total Deductions", border="LTB", fill=True)
    pdf.cell(amount_w, 8, format_currency(slip.deductions), border="RTB", fill=True, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(6)


def _render_net_pay(pdf: _SalarySlipPDF, slip: SalarySlipData) -> None:
    full_w = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.set_fill_color(16, 122, 87)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(full_w * 0.6, 11, "  NET SALARY PAYABLE", fill=True)
    pdf.cell(full_w * 0.4, 11, format_currency(slip.net_salary) + "  ", fill=True, align="R",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)
    pdf.set_font("Helvetica", "I", 9)
    pdf.multi_cell(0, 5, f"Amount in words: {amount_in_words(slip.net_salary)}")


# --------------------------------------------------------------------------- #
# Amount-in-words (Indian numbering: lakh / crore)
# --------------------------------------------------------------------------- #

_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
    "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
    "Seventeen", "Eighteen", "Nineteen",
]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digit_words(n: int) -> str:
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three_digit_words(n: int) -> str:
    hundreds, rest = divmod(n, 100)
    parts = []
    if hundreds:
        parts.append(_ONES[hundreds] + " Hundred")
    if rest:
        parts.append(_two_digit_words(rest))
    return " ".join(parts)


def amount_in_words(amount: Decimal) -> str:
    """Convert a rupee amount to Indian-numbering words (with paise)."""
    quantized = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rupees = int(quantized)
    paise = int((quantized - rupees) * 100)

    if rupees == 0:
        words = "Zero"
    else:
        crore, remainder = divmod(rupees, 10_000_000)
        lakh, remainder = divmod(remainder, 100_000)
        thousand, hundred = divmod(remainder, 1_000)
        segments = []
        if crore:
            segments.append(_three_digit_words(crore) + " Crore")
        if lakh:
            segments.append(_two_digit_words(lakh) + " Lakh")
        if thousand:
            segments.append(_two_digit_words(thousand) + " Thousand")
        if hundred:
            segments.append(_three_digit_words(hundred))
        words = " ".join(segments)

    result = f"Rupees {words}"
    if paise:
        result += f" and {_two_digit_words(paise)} Paise"
    return result + " Only"


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def generate_salary_slip(
    slip: SalarySlipData,
    *,
    company_name: str = DEFAULT_COMPANY_NAME,
    password: str | None = None,
) -> bytes:
    """Render one salary slip to PDF bytes, optionally password-protected.

    Args:
        slip: validated, merged record for one employee/period.
        company_name: header label (move to settings when you wire the route).
        password: if provided, the PDF is AES-256 encrypted with it.

    Returns:
        The PDF file as bytes, ready to attach to an email.
    """
    pdf = _SalarySlipPDF(company_name=company_name, period=slip.month_year)
    pdf.add_page()
    _render_employee_block(pdf, slip)
    _render_earnings_deductions(pdf, slip)
    _render_net_pay(pdf, slip)

    raw = bytes(pdf.output())
    if password is None:
        return raw
    return _encrypt(raw, password)


def _encrypt(pdf_bytes: bytes, password: str) -> bytes:
    """Encrypt PDF bytes with AES-256 using pypdf."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password=password, owner_password=password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()