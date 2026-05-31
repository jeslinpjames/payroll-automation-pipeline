"""Tests for core.file_parser — parsing, validation, joining, and the
net-salary formula."""

from decimal import Decimal

import pytest

from core.file_parser import (
    DataValidationError,
    EmptyFileError,
    MissingColumnsError,
    UnsupportedFileTypeError,
    merge_records,
    parse_employees,
    parse_salaries,
    parse_payroll,
)

EMPLOYEES = (
    b"Employee ID,Full Name,Email Address,Job Title\n"
    b"EMP1001,Aarav Sharma,aarav@example.com,Senior Engineer\n"
    b"EMP1002,Priya Nair,priya@example.com,Analyst\n"
    b"0034,Rohan Das,rohan@example.com,Manager\n"
)

# Varied headers, currency symbol, commas, decimals, and a Month/Year header
# (slash) — plus EMP9999 with no matching employee.
SALARIES = (
    "emp_id,Basic,HRA,Allowances,Deductions,Month/Year\n"
    "EMP1001,\"\u20b91,20,000\",48000,15000,9500,May 2026\n"
    "EMP1002,80000,32000,10000.50,6200,May 2026\n"
    "0034,150000,60000,25000,18000,May 2026\n"
    "EMP9999,50000,20000,5000,3000,May 2026\n"
).encode()


def test_parse_employees_with_varied_headers():
    employees = parse_employees(EMPLOYEES, "employees.csv")
    assert len(employees) == 3
    assert employees[0].name == "Aarav Sharma"
    assert employees[0].designation == "Senior Engineer"


def test_string_employee_id_is_preserved():
    employees = parse_employees(EMPLOYEES, "employees.csv")
    assert any(e.employee_id == "0034" for e in employees)  # not coerced to 34


def test_currency_symbols_and_decimals_parse():
    salaries = parse_salaries(SALARIES, "salary.csv")
    by_id = {s.employee_id: s for s in salaries}
    assert by_id["EMP1001"].base_salary == Decimal("120000")  # ₹1,20,000
    assert by_id["EMP1002"].allowances == Decimal("10000.50")


def test_month_year_header_with_slash_is_recognised():
    salaries = parse_salaries(SALARIES, "salary.csv")
    assert salaries[0].month_year == "May 2026"


def test_net_salary_formula():
    result = parse_payroll(EMPLOYEES, "e.csv", SALARIES, "s.csv")
    slip = next(s for s in result.slips if s.employee_id == "EMP1001")
    # (120000 + 48000 + 15000) - 9500
    assert slip.gross_salary == Decimal("183000")
    assert slip.net_salary == Decimal("173500")


def test_unmatched_salary_rows_are_flagged():
    result = parse_payroll(EMPLOYEES, "e.csv", SALARIES, "s.csv")
    assert result.unmatched_employee_ids == ["EMP9999"]
    assert len(result.slips) == 3


def test_missing_columns_raises():
    with pytest.raises(MissingColumnsError):
        parse_employees(b"Employee ID,Email\nEMP1001,a@b.com\n", "bad.csv")


def test_invalid_number_raises_with_row_context():
    bad = b"emp_id,Basic,HRA,Allowances,Deductions,Month\nEMP1001,abc,1,1,1,May 2026\n"
    with pytest.raises(DataValidationError) as exc:
        parse_salaries(bad, "bad.csv")
    assert "base_salary" in str(exc.value)


def test_duplicate_employee_ids_raise():
    dup = (
        b"Employee ID,Name,Email,Designation\n"
        b"EMP1,A,a@b.com,X\n"
        b"EMP1,B,b@b.com,Y\n"
    )
    with pytest.raises(DataValidationError):
        parse_employees(dup, "dup.csv")


def test_unsupported_file_type_raises():
    with pytest.raises(UnsupportedFileTypeError):
        parse_employees(b"whatever", "data.txt")


def test_empty_file_raises():
    with pytest.raises(EmptyFileError):
        parse_employees(b"", "empty.csv")


def test_merge_returns_unmatched_separately():
    employees = parse_employees(EMPLOYEES, "e.csv")
    salaries = parse_salaries(SALARIES, "s.csv")
    slips, unmatched = merge_records(employees, salaries)
    assert len(slips) == 3
    assert unmatched == ["EMP9999"]