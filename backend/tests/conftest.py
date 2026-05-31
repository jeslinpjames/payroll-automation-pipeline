"""Shared pytest setup.

Points the employee store at a throwaway SQLite file BEFORE the app/config
modules are imported, so tests never touch a real payroll.db. Also exposes
the templates directory by absolute path so email tests render regardless of
the current working directory.
"""
import os
import tempfile

os.environ.setdefault(
    "DATABASE_PATH", os.path.join(tempfile.mkdtemp(), "test_payroll.db")
)

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "..", "templates")