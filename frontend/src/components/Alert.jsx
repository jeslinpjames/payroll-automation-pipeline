export default function Alert({ type = 'info', children, onClose }) {
  if (!children) return null
  return (
    <div className={`alert alert--${type}`} role="alert">
      <span className="alert__text">{children}</span>
      {onClose && (
        <button className="alert__close" onClick={onClose} aria-label="Dismiss">
          ×
        </button>
      )}
    </div>
  )
}