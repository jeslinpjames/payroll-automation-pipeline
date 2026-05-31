export default function ResultsPanel({ result }) {
  const { emails_sent, generated_count, sent_count, failed_count, results } = result

  return (
    <div className="results">
      <div className="stat-row">
        <Stat label="PDFs Generated" value={generated_count} accent />
        <Stat label="Emails Sent" value={emails_sent ? sent_count : '—'} />
        <Stat label="Failed" value={emails_sent ? failed_count : '—'} warn={failed_count > 0} />
      </div>

      {!emails_sent && (
        <div className="alert alert--info" role="alert">
          <span className="alert__text">
            Dry run complete — {generated_count} PDF(s) generated, no emails sent. Enable
            “Send emails” to dispatch them.
          </span>
        </div>
      )}

      {emails_sent && results.length > 0 && (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Status</th>
                <th>ID</th>
                <th>Recipient</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {results.map((r) => (
                <tr key={r.employee_id}>
                  <td>
                    <span className={`pill ${r.success ? 'pill--ok' : 'pill--fail'}`}>
                      {r.success ? 'Sent' : 'Failed'}
                    </span>
                  </td>
                  <td className="mono">{r.employee_id}</td>
                  <td>{r.recipient_email}</td>
                  <td className="muted">{r.error || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
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