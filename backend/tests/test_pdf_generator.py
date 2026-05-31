"""Tests for core.pdf_generator — PDF output, encryption, and formatting."""

import io
from decimal import Decimal

from pypdf import PdfReader

from core.file_parser import SalarySlipData
from core.pdf_generator import (
    amount_in_words,
    derive_pdf_password,
    format_currency,
    generate_salary_slip,
)


def _slip():
    return SalarySlipData(
        employee_id="EMP1001",
        name="Aarav Sharma",
        email="aarav@example.com",
        designation="Senior Engineer",
        base_salary=Decimal("120000"),
        hra=Decimal("48000"),
        allowances=Decimal("15000"),
        deductions=Decimal("9500"),
        month_year="May 2026",
    )


def test_generates_valid_single_page_pdf():
    pdf = generate_salary_slip(_slip())
    assert pdf[:4] == b"%PDF"
    reader = PdfReader(io.BytesIO(pdf))
    assert not reader.is_encrypted
    assert len(reader.pages) == 1


def test_password_protection_round_trip():
    slip = _slip()
    pwd = derive_pdf_password(slip)
    pdf = generate_salary_slip(slip, password=pwd)
    reader = PdfReader(io.BytesIO(pdf))
    assert reader.is_encrypted
    # Wrong password fails, correct password unlocks.
    assert PdfReader(io.BytesIO(pdf)).decrypt("wrong-pw") == 0
    good = PdfReader(io.BytesIO(pdf))
    assert good.decrypt(pwd) != 0
    assert len(good.pages) == 1


def test_derive_password_scheme():
    slip = _slip()
    assert derive_pdf_password(slip) == "aaraEMP1001"
    assert derive_pdf_password(slip, birth_year=1998) == "aara1998"


def test_currency_uses_indian_grouping():
    assert format_currency(Decimal("12345678.50")) == "Rs. 1,23,45,678.50"
    assert format_currency(Decimal("173500")) == "Rs. 1,73,500.00"


def test_amount_in_words():
    assert amount_in_words(Decimal("173500")) == (
        "Rupees One Lakh Seventy Three Thousand Five Hundred Only"
    )