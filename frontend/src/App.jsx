import { useState } from 'react'
import { api } from './api'
import Stepper from './components/Stepper'
import Alert from './components/Alert'
import UploadPanel from './components/UploadPanel'
import PreviewTable from './components/PreviewTable'
import ResultsPanel from './components/ResultsPanel'

export default function App() {
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  const [employeeCount, setEmployeeCount] = useState(0)
  const [salaryFile, setSalaryFile] = useState(null)
  const [preview, setPreview] = useState(null)
  const [result, setResult] = useState(null)

  const [passwordProtect, setPasswordProtect] = useState(true)
  const [sendEmail, setSendEmail] = useState(true)

  const run = async (fn) => {
    setError(null)
    setBusy(true)
    try {
      await fn()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const handleEmployees = (file) =>
    run(async () => {
      const res = await api.uploadEmployees(file)
      setEmployeeCount(res.stored)
      setNotice(`${res.stored} employee record(s) stored.`)
      setStep(1)
    })

  const handleSalary = (file) =>
    run(async () => {
      const res = await api.previewPayroll(file)
      setSalaryFile(file)
      setPreview(res)
      setNotice(null)
      setStep(2)
    })

  const handleProcess = () =>
    run(async () => {
      const res = await api.processPayroll(salaryFile, { sendEmail, passwordProtect })
      setResult(res)
      setStep(3)
    })

  const reset = () => {
    setStep(0)
    setError(null)
    setNotice(null)
    setSalaryFile(null)
    setPreview(null)
    setResult(null)
  }

  return (
    <div className="page">
      <header className="masthead">
        <div className="masthead__brand">
          <span className="masthead__mark">NT</span>
          <div>
            <h1 className="masthead__title">Payroll Automation</h1>
            <p className="masthead__sub">Nippon Toyota · Salary Slip Engine</p>
          </div>
        </div>
        {step > 0 && (
          <button className="btn btn--ghost" onClick={reset}>
            Start over
          </button>
        )}
      </header>

      <Stepper current={step} />

      <main className="stack">
        <Alert type="error" onClose={() => setError(null)}>
          {error}
        </Alert>
        {notice && (
          <Alert type="success" onClose={() => setNotice(null)}>
            {notice}
          </Alert>
        )}

        {/* Step 1 — Employees */}
        <section className={`card ${step === 0 ? 'card--active' : ''}`} style={{ animationDelay: '0ms' }}>
          <CardHead n="01" title="Upload employee master" done={step > 0} />
          <p className="card__lead">
            Upload the roster (Employee ID, Name, Email, Designation). It is stored so monthly
            salary sheets can be matched by Employee ID.
          </p>
          {step === 0 ? (
            <UploadPanel
              label="Drop employee master file"
              hint="or click to browse"
              buttonText="Upload & store"
              loading={busy}
              onSubmit={handleEmployees}
            />
          ) : (
            <p className="card__resolved">✓ {employeeCount} employees stored</p>
          )}
        </section>

        {/* Step 2 — Salary sheet */}
        <section className={`card ${step === 1 ? 'card--active' : ''} ${step < 1 ? 'card--locked' : ''}`} style={{ animationDelay: '80ms' }}>
          <CardHead n="02" title="Upload salary sheet" done={step > 1} />
          <p className="card__lead">
            Upload this month’s salary data (Base, HRA, Allowances, Deductions, Month/Year).
            We’ll join it to the roster and preview before sending anything.
          </p>
          {step === 1 && (
            <UploadPanel
              label="Drop monthly salary file"
              hint="or click to browse"
              buttonText="Preview salary slips"
              loading={busy}
              onSubmit={handleSalary}
            />
          )}
          {step > 1 && <p className="card__resolved">✓ {preview?.slips.length} slips matched</p>}
        </section>

        {/* Step 3 — Review + process */}
        <section className={`card ${step === 2 ? 'card--active' : ''} ${step < 2 ? 'card--locked' : ''}`} style={{ animationDelay: '160ms' }}>
          <CardHead n="03" title="Review & dispatch" done={step > 2} />
          {step >= 2 && preview && (
            <PreviewTable preview={preview} salaryFile={salaryFile} onError={setError} />
          )}

          {step === 2 && (
            <div className="dispatch">
              <div className="options">
                <Toggle
                  checked={passwordProtect}
                  onChange={setPasswordProtect}
                  label="Password-protect PDFs"
                  sub="Encrypts each slip (name + Employee ID)"
                />
                <Toggle
                  checked={sendEmail}
                  onChange={setSendEmail}
                  label="Send emails"
                  sub="Off = generate PDFs only (dry run)"
                />
              </div>
              <button
                className="btn btn--primary btn--lg"
                disabled={busy || preview.slips.length === 0}
                onClick={handleProcess}
              >
                {busy
                  ? 'Processing…'
                  : sendEmail
                    ? `Generate & email ${preview.slips.length} slip(s)`
                    : `Generate ${preview.slips.length} slip(s)`}
              </button>
            </div>
          )}
        </section>

        {/* Results */}
        {step === 3 && result && (
          <section className="card card--active" style={{ animationDelay: '0ms' }}>
            <CardHead n="04" title="Results" />
            <ResultsPanel result={result} />
            <button className="btn btn--ghost" onClick={reset}>
              Run another batch
            </button>
          </section>
        )}
      </main>

      <footer className="footer">
        Built with FastAPI + React · {new Date().getFullYear()}
      </footer>
    </div>
  )
}

function CardHead({ n, title, done }) {
  return (
    <div className="card__head">
      <span className={`card__num ${done ? 'card__num--done' : ''}`}>{done ? '✓' : n}</span>
      <h2 className="card__title">{title}</h2>
    </div>
  )
}

function Toggle({ checked, onChange, label, sub }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="toggle__switch" aria-hidden="true" />
      <span className="toggle__text">
        <span className="toggle__label">{label}</span>
        <span className="toggle__sub">{sub}</span>
      </span>
    </label>
  )
}