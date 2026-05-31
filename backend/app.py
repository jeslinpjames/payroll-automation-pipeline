"""FastAPI application entry point.

Run locally with:
    uvicorn app:app --reload

This file is the application's HTTP boundary. It wires up middleware, logging,
and exception translation, and exposes the orchestration endpoints that tie the
core modules together:

    POST /employees        upload + persist the employee master sheet
    GET  /employees        list stored employees
    POST /payroll/preview  upload salary sheet, join stored employees, preview
    POST /payroll/process  generate slip PDFs and email them to employees

All business logic lives in core/; this layer only translates HTTP <-> models.
"""

from __future__ import annotations

import logging
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr

from config import settings
from core.email_dispatcher import (
    BrevoDispatcher,
    EmailDispatcher,
    EmailDispatchError,
    EmailJob,
    MailtrapDispatcher,
)
from core.file_parser import (
    DataValidationError,
    EmptyFileError,
    FileParsingError,
    MissingColumnsError,
    SalarySlipData,
    UnsupportedFileTypeError,
    merge_records,
    parse_employees,
    parse_salaries,
)
from core.pdf_generator import derive_pdf_password, generate_salary_slip
from core.storage import EmployeeStore

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("salary_slip_api")

# Shared employee store (one per process).
store = EmployeeStore(settings.database_path)


# --------------------------------------------------------------------------- #
# Response models (keep the OpenAPI docs precise)
# --------------------------------------------------------------------------- #


class EmployeeOut(BaseModel):
    employee_id: str
    name: str
    email: EmailStr
    designation: str


class UploadEmployeesResponse(BaseModel):
    stored: int
    employees: list[EmployeeOut]


class PreviewResponse(BaseModel):
    slips: list[SalarySlipData]
    employee_count: int
    salary_row_count: int
    unmatched_employee_ids: list[str]


class SendResultOut(BaseModel):
    employee_id: str
    recipient_email: str
    success: bool
    error: str | None = None


class ProcessResponse(BaseModel):
    emails_sent: bool
    generated_count: int
    sent_count: int
    failed_count: int
    unmatched_employee_ids: list[str]
    results: list[SendResultOut]


# --------------------------------------------------------------------------- #
# Lifespan & app factory
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    logger.info("Employee store ready at %s (%d records)", settings.database_path, store.count())
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Automated pipeline for generating and emailing employee salary slips.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    _register_exception_handlers(app)
    _register_routes(app)
    return app


# --------------------------------------------------------------------------- #
# Exception handling
# --------------------------------------------------------------------------- #


def _error_response(code: int, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"error": exc.__class__.__name__, "detail": str(exc)},
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(UnsupportedFileTypeError)
    async def _unsupported(_: Request, exc: UnsupportedFileTypeError) -> JSONResponse:
        return _error_response(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, exc)

    @app.exception_handler(EmptyFileError)
    async def _empty(_: Request, exc: EmptyFileError) -> JSONResponse:
        return _error_response(status.HTTP_400_BAD_REQUEST, exc)

    @app.exception_handler(MissingColumnsError)
    async def _missing(_: Request, exc: MissingColumnsError) -> JSONResponse:
        return _error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, exc)

    @app.exception_handler(DataValidationError)
    async def _invalid(_: Request, exc: DataValidationError) -> JSONResponse:
        return _error_response(status.HTTP_422_UNPROCESSABLE_ENTITY, exc)

    @app.exception_handler(FileParsingError)
    async def _generic_parse(_: Request, exc: FileParsingError) -> JSONResponse:
        logger.warning("Unhandled parsing error: %s", exc)
        return _error_response(status.HTTP_400_BAD_REQUEST, exc)

    # Email/connection failures are upstream problems -> 502 Bad Gateway.
    @app.exception_handler(EmailDispatchError)
    async def _email_error(_: Request, exc: EmailDispatchError) -> JSONResponse:
        logger.error("Email dispatch failure: %s", exc)
        return _error_response(status.HTTP_502_BAD_GATEWAY, exc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


async def _read_upload(file: UploadFile) -> bytes:
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_upload_bytes}-byte limit.",
        )
    return content


_PASSWORD_HINT = "First 4 letters of your name (lowercase) followed by your Employee ID."


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


def _register_routes(app: FastAPI) -> None:
    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": settings.app_version}

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/employees", response_model=UploadEmployeesResponse, tags=["employees"])
    async def upload_employees(file: UploadFile = File(...)) -> UploadEmployeesResponse:
        content = await _read_upload(file)
        employees = parse_employees(content, file.filename or "employees.csv")
        store.upsert_employees(employees)
        logger.info("Stored/updated %d employees", len(employees))
        return UploadEmployeesResponse(
            stored=len(employees),
            employees=[EmployeeOut(**e.model_dump()) for e in employees],
        )

    @app.get("/employees", response_model=list[EmployeeOut], tags=["employees"])
    async def list_employees() -> list[EmployeeOut]:
        return [EmployeeOut(**e.model_dump()) for e in store.get_all()]

    @app.post("/payroll/preview", response_model=PreviewResponse, tags=["payroll"])
    async def preview_payroll(file: UploadFile = File(...)) -> PreviewResponse:
        content = await _read_upload(file)
        salaries = parse_salaries(content, file.filename or "salary.csv")
        employees = store.get_all()
        slips, unmatched = merge_records(employees, salaries)
        return PreviewResponse(
            slips=slips,
            employee_count=len(employees),
            salary_row_count=len(salaries),
            unmatched_employee_ids=unmatched,
        )

    @app.post("/payroll/slip", tags=["payroll"])
    async def get_slip(
        file: UploadFile = File(...),
        employee_id: str = Form(...),
        password_protect: bool = Form(False),
    ) -> Response:
        """Generate and return a single employee's salary slip as a PDF.

        Used by the dashboard's per-row Preview/Download buttons. Defaults to
        an unprotected PDF (the admin is viewing it pre-send); pass
        password_protect=true to mirror the encrypted emailed copy.
        """
        content = await _read_upload(file)
        salaries = parse_salaries(content, file.filename or "salary.csv")
        employees = store.get_all()
        slips, _ = merge_records(employees, salaries)

        slip = next((s for s in slips if s.employee_id == employee_id), None)
        if slip is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No salary slip found for Employee ID '{employee_id}'.",
            )

        pwd = derive_pdf_password(slip) if password_protect else None
        pdf_bytes = generate_salary_slip(
            slip, company_name=settings.company_name, password=pwd
        )
        safe = re.sub(r"[^A-Za-z0-9]+", "_", f"{slip.employee_id}_{slip.month_year}").strip("_")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'inline; filename="SalarySlip_{safe}.pdf"'},
        )

    @app.post("/payroll/process", response_model=ProcessResponse, tags=["payroll"])
    async def process_payroll(
        file: UploadFile = File(...),
        send_email: bool = Form(True),
        password_protect: bool = Form(False),
    ) -> ProcessResponse:
        content = await _read_upload(file)
        salaries = parse_salaries(content, file.filename or "salary.csv")
        employees = store.get_all()
        slips, unmatched = merge_records(employees, salaries)

        jobs: list[EmailJob] = []
        for slip in slips:
            pwd = derive_pdf_password(slip) if password_protect else None
            pdf_bytes = generate_salary_slip(
                slip, company_name=settings.company_name, password=pwd
            )
            jobs.append(
                EmailJob(
                    recipient_email=str(slip.email),
                    recipient_name=slip.name,
                    employee_id=slip.employee_id,
                    month_year=slip.month_year,
                    pdf_bytes=pdf_bytes,
                    password_protected=pwd is not None,
                    password_hint=_PASSWORD_HINT if pwd else None,
                )
            )

        if not send_email:
            # PDFs generated and validated, but delivery skipped (e.g. dry run).
            return ProcessResponse(
                emails_sent=False,
                generated_count=len(jobs),
                sent_count=0,
                failed_count=0,
                unmatched_employee_ids=unmatched,
                results=[],
            )

        if settings.brevo_api_key:
            # Real delivery over HTTPS (works even where SMTP is blocked).
            dispatcher = BrevoDispatcher(
                api_key=settings.brevo_api_key,
                from_email=str(settings.smtp_from_email or settings.smtp_username or "payroll@nippontoyota.com"),
                from_name=settings.smtp_from_name,
                company_name=settings.company_name,
            )
        elif settings.mailtrap_api_token and settings.mailtrap_inbox_id:
            # HTTP API path (HTTPS) — bypasses SMTP entirely.
            dispatcher = MailtrapDispatcher(
                api_token=settings.mailtrap_api_token,
                inbox_id=settings.mailtrap_inbox_id,
                from_email=str(settings.smtp_from_email or "payroll@nippontoyota.com"),
                from_name=settings.smtp_from_name,
                company_name=settings.company_name,
            )
        else:
            dispatcher = EmailDispatcher(
                host=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_username,
                password=settings.smtp_password,
                from_email=str(settings.smtp_from_email or settings.smtp_username),
                from_name=settings.smtp_from_name,
                company_name=settings.company_name,
            )
        results = dispatcher.send_salary_slips(jobs)
        sent = sum(1 for r in results if r.success)
        return ProcessResponse(
            emails_sent=True,
            generated_count=len(jobs),
            sent_count=sent,
            failed_count=len(results) - sent,
            unmatched_employee_ids=unmatched,
            results=[SendResultOut(**r.__dict__) for r in results],
        )


app = create_app()