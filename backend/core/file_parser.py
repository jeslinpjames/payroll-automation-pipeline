"""File parsing and data-extraction layer.

Responsibilities of this module:
    1. Read an uploaded CSV / Excel file (in memory) into a DataFrame.
    2. Normalize messy real-world headers ("Employee ID", "emp_id", ...)
       to a canonical schema.
    3. Validate that required columns and values are present and well-typed.
    4. Merge the employee master sheet with the monthly salary sheet on the
       Employee ID primary key, and surface any rows that fail to match.

The module is deliberately framework-agnostic: it takes raw `bytes` + a
filename and returns plain Pydantic models, so it can be driven by FastAPI,
a CLI, or a test without any changes. Money is handled as `Decimal`
throughout — never float — to avoid rounding errors on payroll figures.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal, InvalidOperation
from typing import Iterable

import pandas as pd
from pydantic import BaseModel, EmailStr, computed_field, field_validator

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class FileParsingError(Exception):
    """Base class for every error raised by this module.

    Carrying a single base class lets the API layer register one exception
    handler and translate the whole family into clean HTTP responses.
    """


class EmptyFileError(FileParsingError):
    """The uploaded file contained no data rows."""


class UnsupportedFileTypeError(FileParsingError):
    """The uploaded file's extension is not a supported tabular format."""


class MissingColumnsError(FileParsingError):
    """One or more required columns are absent after header normalization."""

    def __init__(self, missing: Iterable[str], available: Iterable[str]) -> None:
        self.missing = list(missing)
        self.available = list(available)
        super().__init__(
            f"Missing required columns: {self.missing}. "
            f"Columns found in file: {self.available}"
        )


class DataValidationError(FileParsingError):
    """A cell value could not be parsed or violated a business rule."""


# --------------------------------------------------------------------------- #
# Canonical schema + header aliases
# --------------------------------------------------------------------------- #

SUPPORTED_EXTENSIONS = (".csv", ".xlsx", ".xlsm", ".xls")

# Map of canonical_field -> set of accepted (already-normalized) header spellings.
# Normalization lowercases, trims, and collapses spaces/underscores/hyphens,
# so "Employee ID", "employee_id" and "Employee-Id" all become "employeeid".
_EMPLOYEE_ALIASES: dict[str, set[str]] = {
    "employee_id": {"employeeid", "empid", "id", "employeenumber", "empno"},
    "name": {"name", "employeename", "fullname"},
    "email": {"email", "emailaddress", "mail", "employeeemail"},
    "designation": {"designation", "title", "role", "jobtitle", "position"},
}

_SALARY_ALIASES: dict[str, set[str]] = {
    "employee_id": {"employeeid", "empid", "id", "employeenumber", "empno"},
    "base_salary": {"basesalary", "basic", "basicsalary", "base"},
    "hra": {"hra", "houserentallowance"},
    "allowances": {"allowances", "allowance", "otherallowances", "specialallowance"},
    "deductions": {"deductions", "deduction", "totaldeductions", "tax"},
    "month_year": {"monthyear", "month", "period", "salarymonth", "payperiod"},
}


# --------------------------------------------------------------------------- #
# Pydantic models
# --------------------------------------------------------------------------- #


class EmployeeRecord(BaseModel):
    """A single row from the employee master sheet."""

    employee_id: str
    name: str
    email: EmailStr
    designation: str

    @field_validator("employee_id", "name", "designation")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class SalaryRecord(BaseModel):
    """A single row from the monthly salary sheet."""

    employee_id: str
    base_salary: Decimal
    hra: Decimal
    allowances: Decimal
    deductions: Decimal
    month_year: str

    @field_validator("employee_id", "month_year")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned


class SalarySlipData(BaseModel):
    """Merged, slip-ready record for exactly one employee for one period.

    `net_salary` is a derived field computed from the components, so the PDF
    generator and email layer read it straight off the model — there is one
    and only one place where the payroll formula lives.
    """

    employee_id: str
    name: str
    email: EmailStr
    designation: str
    base_salary: Decimal
    hra: Decimal
    allowances: Decimal
    deductions: Decimal
    month_year: str

    @computed_field
    @property
    def gross_salary(self) -> Decimal:
        return self.base_salary + self.hra + self.allowances

    @computed_field
    @property
    def net_salary(self) -> Decimal:
        # Net = (Base + HRA + Allowances) - Deductions
        return self.gross_salary - self.deductions


class ParseResult(BaseModel):
    """Everything the API needs to render a preview and run the pipeline."""

    slips: list[SalarySlipData]
    employee_count: int
    salary_row_count: int
    # Salary rows whose Employee ID had no matching master record.
    unmatched_employee_ids: list[str]


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #


def _normalize_header(header: str) -> str:
    """Reduce a header to a comparable key: lowercase, alphanumerics only.

    Strips spaces, underscores, hyphens, slashes, dots, parentheses, etc. so
    "Employee ID", "employee_id", "Employee-Id" and "Month/Year" all collapse
    to a single comparable token.
    """
    return re.sub(r"[^a-z0-9]", "", str(header).strip().lower())


def _read_tabular(content: bytes, filename: str) -> pd.DataFrame:
    """Read raw bytes into a DataFrame based on the file extension.

    Everything is read as string (dtype=str) so identifiers like "001" are
    not silently coerced to integers — we control type conversion ourselves.
    """
    if not content:
        raise EmptyFileError("The uploaded file is empty.")

    lower = filename.lower()
    buffer = io.BytesIO(content)

    try:
        if lower.endswith(".csv"):
            df = pd.read_csv(buffer, dtype=str, keep_default_na=False, skip_blank_lines=True)
        elif lower.endswith((".xlsx", ".xlsm")):
            df = pd.read_excel(buffer, dtype=str, engine="openpyxl")
        elif lower.endswith(".xls"):
            df = pd.read_excel(buffer, dtype=str, engine="xlrd")
        else:
            raise UnsupportedFileTypeError(
                f"Unsupported file type: '{filename}'. "
                f"Supported extensions: {', '.join(SUPPORTED_EXTENSIONS)}."
            )
    except UnsupportedFileTypeError:
        raise
    except Exception as exc:  # pragma: no cover - pandas raises many error types
        raise FileParsingError(f"Could not read '{filename}': {exc}") from exc

    # Excel reads missing cells as NaN even with dtype=str; make them empty.
    df = df.fillna("")
    if df.empty:
        raise EmptyFileError(f"'{filename}' contains headers but no data rows.")
    return df


def _apply_canonical_schema(df: pd.DataFrame, aliases: dict[str, set[str]]) -> pd.DataFrame:
    """Rename columns to canonical names and verify all required ones exist."""
    normalized_to_actual = {_normalize_header(col): col for col in df.columns}

    rename_map: dict[str, str] = {}
    for canonical, accepted in aliases.items():
        for norm_key, actual in normalized_to_actual.items():
            if norm_key in accepted:
                rename_map[actual] = canonical
                break

    df = df.rename(columns=rename_map)

    missing = [field for field in aliases if field not in df.columns]
    if missing:
        raise MissingColumnsError(missing=missing, available=list(df.columns))

    # Keep only the canonical columns, in a stable order.
    return df[list(aliases.keys())]


def _to_decimal(value: object, *, field: str, row: int) -> Decimal:
    """Coerce a cell into a non-negative Decimal, tolerating commas/symbols."""
    raw = str(value).strip()
    if raw == "":
        raise DataValidationError(f"Row {row}: '{field}' is empty.")

    # Strip currency symbols and thousands separators: "₹1,200.50" -> "1200.50"
    cleaned = "".join(ch for ch in raw if ch.isdigit() or ch in ".-")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError) as exc:
        raise DataValidationError(
            f"Row {row}: '{field}' value '{raw}' is not a valid number."
        ) from exc

    if amount < 0:
        raise DataValidationError(f"Row {row}: '{field}' must not be negative ({raw}).")
    return amount


# --------------------------------------------------------------------------- #
# Public parsing functions
# --------------------------------------------------------------------------- #


def parse_employees(content: bytes, filename: str) -> list[EmployeeRecord]:
    """Parse and validate the employee master sheet."""
    df = _apply_canonical_schema(_read_tabular(content, filename), _EMPLOYEE_ALIASES)

    records: list[EmployeeRecord] = []
    for offset, row in enumerate(df.to_dict(orient="records"), start=2):  # row 1 = header
        try:
            records.append(
                EmployeeRecord(
                    employee_id=str(row["employee_id"]),
                    name=str(row["name"]),
                    email=str(row["email"]).strip(),
                    designation=str(row["designation"]),
                )
            )
        except Exception as exc:
            raise DataValidationError(f"Row {offset} (employees): {exc}") from exc

    _assert_unique_ids((r.employee_id for r in records), context="employee master")
    return records


def parse_salaries(content: bytes, filename: str) -> list[SalaryRecord]:
    """Parse and validate the monthly salary sheet."""
    df = _apply_canonical_schema(_read_tabular(content, filename), _SALARY_ALIASES)

    records: list[SalaryRecord] = []
    for offset, row in enumerate(df.to_dict(orient="records"), start=2):
        records.append(
            SalaryRecord(
                employee_id=str(row["employee_id"]).strip(),
                base_salary=_to_decimal(row["base_salary"], field="base_salary", row=offset),
                hra=_to_decimal(row["hra"], field="hra", row=offset),
                allowances=_to_decimal(row["allowances"], field="allowances", row=offset),
                deductions=_to_decimal(row["deductions"], field="deductions", row=offset),
                month_year=str(row["month_year"]),
            )
        )

    _assert_unique_ids((r.employee_id for r in records), context="salary sheet")
    return records


def merge_records(
    employees: list[EmployeeRecord],
    salaries: list[SalaryRecord],
) -> tuple[list[SalarySlipData], list[str]]:
    """Join salary rows onto employee master records by Employee ID.

    Returns the slip-ready records plus the list of Employee IDs that appeared
    in the salary sheet but had no matching master record (so the caller can
    warn the admin instead of silently dropping people).
    """
    index = {emp.employee_id: emp for emp in employees}
    slips: list[SalarySlipData] = []
    unmatched: list[str] = []

    for salary in salaries:
        employee = index.get(salary.employee_id)
        if employee is None:
            unmatched.append(salary.employee_id)
            continue
        slips.append(
            SalarySlipData(
                employee_id=employee.employee_id,
                name=employee.name,
                email=employee.email,
                designation=employee.designation,
                base_salary=salary.base_salary,
                hra=salary.hra,
                allowances=salary.allowances,
                deductions=salary.deductions,
                month_year=salary.month_year,
            )
        )
    return slips, unmatched


def parse_payroll(
    employee_content: bytes,
    employee_filename: str,
    salary_content: bytes,
    salary_filename: str,
) -> ParseResult:
    """End-to-end convenience entry point used by the upload endpoint."""
    employees = parse_employees(employee_content, employee_filename)
    salaries = parse_salaries(salary_content, salary_filename)
    slips, unmatched = merge_records(employees, salaries)
    return ParseResult(
        slips=slips,
        employee_count=len(employees),
        salary_row_count=len(salaries),
        unmatched_employee_ids=unmatched,
    )


# --------------------------------------------------------------------------- #
# Internal validation helpers
# --------------------------------------------------------------------------- #


def _assert_unique_ids(ids: Iterable[str], *, context: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for emp_id in ids:
        if emp_id in seen:
            duplicates.add(emp_id)
        seen.add(emp_id)
    if duplicates:
        raise DataValidationError(
            f"Duplicate Employee IDs in {context}: {sorted(duplicates)}."
        )