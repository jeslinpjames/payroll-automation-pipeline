import { useState } from 'react'
import { api, formatINR } from '../api'

export default function PreviewTable({ preview, salaryFile, onError }) {
  const { slips, employee_count, salary_row_count, unmatched_employee_ids } = preview
  const [busy, setBusy] = useState(null) // `${employee_id}:${mode}` currently loading

  const openSlip = async (employeeId, mode) => {
    if (!salaryFile) {
      onError?.('Salary file is no longer in memory — re-run the preview.')
      return
    }
    setBusy(`${employeeId}:${mode}`)
    try {
      const blob = await api.getSlipBlob(salaryFile, employeeId)
      const url = URL.createObjectURL(blob)
      if (mode === 'preview') {
        window.open(url, '_blank', 'noopener')
        setTimeout(() => URL.revokeObjectURL(url), 60000)
      } else {
        const a = document.createElement('a')
        a.href = url
        a.download = `SalarySlip_${employeeId}.pdf`
        document.body.appendChild(a)
        a.click()
        a.remove()
        URL.revokeObjectURL(url)
      }
    } catch (e) {
      onError?.(e.message)
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="preview">
      <div className="stat-row">
        <Stat label="Stored Employees" value={employee_count} />
        <Stat label="Salary Rows" value={salary_row_count} />
        <Stat label="Slips Ready" value={slips.length} accent />
        <Stat label="Unmatched" value={unmatched_employee_ids.length} warn={unmatched_employee_ids.length > 0} />
      </div>

      {unmatched_employee_ids.length > 0 && (
        <div className="alert alert--error" role="alert">
          <span className="alert__text">
            {unmatched_employee_ids.length} salary row(s) have no matching employee and will be
            skipped: {unmatched_employee_ids.join(', ')}
          </span>
        </div>
      )}

      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>Name</th>
              <th>Designation</th>
              <th className="num">Basic</th>
              <th className="num">HRA</th>
              <th className="num">Allow.</th>
              <th className="num">Deduct.</th>
              <th className="num">Net Salary</th>
              <th>Slip</th>
            </tr>
          </thead>
          <tbody>
            {slips.map((s) => (
              <tr key={s.employee_id}>
                <td className="mono">{s.employee_id}</td>
                <td>{s.name}</td>
                <td className="muted">{s.designation}</td>
                <td className="num">{formatINR(s.base_salary)}</td>
                <td className="num">{formatINR(s.hra)}</td>
                <td className="num">{formatINR(s.allowances)}</td>
                <td className="num">{formatINR(s.deductions)}</td>
                <td className="num num--net">{formatINR(s.net_salary)}</td>
                <td className="row-actions">
                  <button
                    className="btn-link"
                    disabled={busy !== null}
                    onClick={() => openSlip(s.employee_id, 'preview')}
                  >
                    {busy === `${s.employee_id}:preview` ? '…' : 'Preview'}
                  </button>
                  <button
                    className="btn-link"
                    disabled={busy !== null}
                    onClick={() => openSlip(s.employee_id, 'download')}
                  >
                    {busy === `${s.employee_id}:download` ? '…' : 'Download'}
                  </button>
                </td>
              </tr>
            ))}
            {slips.length === 0 && (
              <tr>
                <td colSpan={9} className="empty-cell">
                  No matching salary slips. Upload employees first, then a salary sheet whose
                  Employee IDs match.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function Stat({ label, value, accent, warn }) {
  return (
    <div className={`stat ${accent ? 'stat--accent' : ''} ${warn ? 'stat--warn' : ''}`}>
      <span className="stat__value">{value}</span>
      <span className="stat__label">{label}</span>
    </div>
  )
}