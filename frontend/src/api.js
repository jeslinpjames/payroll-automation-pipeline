// Single source of truth for talking to the FastAPI backend.
// The base URL is injected at build time so the same code runs locally and
// against the deployed backend (set VITE_API_BASE_URL in Vercel).

const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request(path, { method = 'GET', body } = {}) {
  let res
  try {
    res = await fetch(`${BASE_URL}${path}`, { method, body })
  } catch {
    // Network-level failure (server down, CORS, wrong URL).
    throw new Error(
      `Could not reach the backend at ${BASE_URL}. Is the API running?`,
    )
  }

  const isJson = res.headers.get('content-type')?.includes('application/json')
  const data = isJson ? await res.json() : null

  if (!res.ok) {
    // The backend returns a consistent { error, detail } envelope.
    const detail = data?.detail || data?.error || `Request failed (${res.status})`
    throw new Error(detail)
  }
  return data
}

function fileForm(file, extra = {}) {
  const fd = new FormData()
  fd.append('file', file)
  for (const [key, value] of Object.entries(extra)) {
    fd.append(key, String(value))
  }
  return fd
}

export const api = {
  uploadEmployees: (file) =>
    request('/employees', { method: 'POST', body: fileForm(file) }),

  listEmployees: () => request('/employees'),

  previewPayroll: (file) =>
    request('/payroll/preview', { method: 'POST', body: fileForm(file) }),

  processPayroll: (file, { sendEmail, passwordProtect }) =>
    request('/payroll/process', {
      method: 'POST',
      body: fileForm(file, {
        send_email: sendEmail,
        password_protect: passwordProtect,
      }),
    }),
}

// Format a number as Indian rupees with lakh/crore digit grouping.
export function formatINR(value) {
  const num = typeof value === 'string' ? parseFloat(value) : value
  if (num === null || num === undefined || Number.isNaN(num)) return '—'

  const [whole, frac] = Math.abs(num).toFixed(2).split('.')
  let lastThree = whole.slice(-3)
  let rest = whole.slice(0, -3)
  if (rest) {
    rest = rest.replace(/\B(?=(\d{2})+(?!\d))/g, ',')
    lastThree = ',' + lastThree
  }
  return `${num < 0 ? '-' : ''}₹${rest}${lastThree}.${frac}`
}