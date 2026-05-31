import { useRef, useState } from 'react'

const ACCEPT = '.csv,.xlsx,.xls'

export default function UploadPanel({ label, hint, buttonText, loading, onSubmit }) {
  const [file, setFile] = useState(null)
  const [dragging, setDragging] = useState(false)
  const inputRef = useRef(null)

  const pick = (f) => {
    if (f) setFile(f)
  }

  const onDrop = (e) => {
    e.preventDefault()
    setDragging(false)
    pick(e.dataTransfer.files?.[0])
  }

  return (
    <div className="upload">
      <div
        className={`dropzone ${dragging ? 'dropzone--active' : ''} ${file ? 'dropzone--filled' : ''}`}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === 'Enter' && inputRef.current?.click()}
      >
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          hidden
          onChange={(e) => pick(e.target.files?.[0])}
        />
        {file ? (
          <div className="dropzone__file">
            <span className="dropzone__filename">{file.name}</span>
            <span className="dropzone__meta">{(file.size / 1024).toFixed(1)} KB · click to replace</span>
          </div>
        ) : (
          <div className="dropzone__empty">
            <span className="dropzone__title">{label}</span>
            <span className="dropzone__hint">{hint}</span>
            <span className="dropzone__formats">CSV · XLSX · XLS</span>
          </div>
        )}
      </div>

      <button
        className="btn btn--primary"
        disabled={!file || loading}
        onClick={() => onSubmit(file)}
      >
        {loading ? 'Working…' : buttonText}
      </button>
    </div>
  )
}