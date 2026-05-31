import { formatINR } from '../api'

export default function PreviewTable({ preview }) {
  const { slips, employee_count, salary_row_count, unmatched_employee_ids } = preview

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
              </tr>
            ))}
            {slips.length === 0 && (
              <tr>
                <td colSpan={8} className="empty-cell">
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