"""FastAPI application entry point.

Run locally with:
    uvicorn app:app --reload

This file is intentionally thin: it wires up the application (CORS, logging,
exception translation, health checks) and is the single place where HTTP
concerns live. Business logic stays in `core/`. The payroll upload route is
added here once the PDF generator and email dispatcher are in place.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from core.file_parser import (
    DataValidationError,
    EmptyFileError,
    FileParsingError,
    MissingColumnsError,
    UnsupportedFileTypeError,
)

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.DEBUG if settings.debug else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("salary_slip_api")


# --------------------------------------------------------------------------- #
# Lifespan
# --------------------------------------------------------------------------- #


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.app_name, settings.app_version)
    yield
    logger.info("Shutting down %s", settings.app_name)


# --------------------------------------------------------------------------- #
# Application factory
# --------------------------------------------------------------------------- #


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


def _register_exception_handlers(app: FastAPI) -> None:
    """Translate the file_parser exception family into clean HTTP responses.

    Each handler returns a consistent JSON envelope: {"error": <type>,
    "detail": <message>} so the frontend can render predictable messages.
    """

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

    # Catch-all for the base class so nothing in this family ever 500s.
    @app.exception_handler(FileParsingError)
    async def _generic_parse(_: Request, exc: FileParsingError) -> JSONResponse:
        logger.warning("Unhandled parsing error: %s", exc)
        return _error_response(status.HTTP_400_BAD_REQUEST, exc)


def _error_response(code: int, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=code,
        content={"error": exc.__class__.__name__, "detail": str(exc)},
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


def _register_routes(app: FastAPI) -> None:
    @app.get("/", tags=["meta"])
    async def root() -> dict[str, str]:
        return {"service": settings.app_name, "version": settings.app_version}

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        """Liveness probe — used by Render/AWS health checks."""
        return {"status": "ok"}

    # NOTE: the payroll upload endpoint is added in the next step, once
    # core/pdf_generator.py and core/email_dispatcher.py exist. It will call
    # core.file_parser.parse_payroll(...) and stream back a preview + results.


app = create_app()