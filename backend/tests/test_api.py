"""API integration tests with FastAPI's TestClient. The employee store is
cleared before each test for isolation."""

import pytest
from fastapi.testclient import TestClient

import app as appmod

client = TestClient(appmod.app)

EMPLOYEES = (
    b"Employee ID,Name,Email,Designation\n"
    b"EMP1001,Aarav Sharma,aarav@example.com,Senior Engineer\n"
    b"EMP1002,Priya Nair,priya@example.com,Analyst\n"
)
SALARIES = (
    b"Employee ID,Base Salary,HRA,Allowances,Deductions,Month/Year\n"
    b"EMP1001,120000,48000,15000,9500,May 2026\n"
    b"EMP1002,80000,32000,10000,6200,May 2026\n"
    b"EMP9999,50000,20000,5000,3000,May 2026\n"
)


@pytest.fixture(autouse=True)
def _clear_store():
    appmod.store.clear()
    yield


def _csv(name, data):
    return {"file": (name, data, "text/csv")}


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_upload_employees_and_list():
    r = client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    assert r.status_code == 200
    assert r.json()["stored"] == 2
    assert len(client.get("/employees").json()) == 2


def test_preview_flags_unmatched_and_computes_net():
    client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    r = client.post("/payroll/preview", files=_csv("s.csv", SALARIES))
    body = r.json()
    assert r.status_code == 200
    assert len(body["slips"]) == 2
    assert body["unmatched_employee_ids"] == ["EMP9999"]
    e001 = next(s for s in body["slips"] if s["employee_id"] == "EMP1001")
    assert str(e001["net_salary"]) in ("173500", "173500.00", "173500.0")


def test_slip_endpoint_returns_pdf():
    client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    r = client.post("/payroll/slip", files=_csv("s.csv", SALARIES), data={"employee_id": "EMP1001"})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_slip_unknown_employee_returns_404():
    client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    r = client.post("/payroll/slip", files=_csv("s.csv", SALARIES), data={"employee_id": "NOPE"})
    assert r.status_code == 404


def test_process_dry_run_generates_without_sending():
    client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    r = client.post(
        "/payroll/process",
        files=_csv("s.csv", SALARIES),
        data={"send_email": "false", "password_protect": "true"},
    )
    body = r.json()
    assert r.status_code == 200
    assert body["emails_sent"] is False
    assert body["generated_count"] == 2


def test_bad_file_returns_422_with_envelope():
    client.post("/employees", files=_csv("e.csv", EMPLOYEES))
    r = client.post("/payroll/preview", files=_csv("bad.csv", b"Employee ID\nEMP1001\n"))
    assert r.status_code == 422
    assert r.json()["error"] == "MissingColumnsError"