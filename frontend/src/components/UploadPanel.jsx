import { useRef, useState } from 'react'
import { uploadSources } from '../services/api'

const ACCEPT = '.db,.sqlite,.sqlite3,.csv,.tsv,.txt,.xlsx,.xlsm,.sql'

export default function UploadPanel({ onCreated, onError }) {
  const [files, setFiles] = useState([])
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef(null)

  const collect = (list) => {
    setFiles((current) => {
      const seen = new Set(current.map((file) => `${file.name}:${file.size}`))
      const incoming = Array.from(list).filter((file) => !seen.has(`${file.name}:${file.size}`))
      return [...current, ...incoming].slice(0, 40)
    })
  }

  const submit = () => {
    setBusy(true)
    uploadSources(files)
      .then((result) => {
        setFiles([])
        if (inputRef.current) inputRef.current.value = ''
        onCreated(result)
      })
      .catch((exc) => onError(exc.message))
      .finally(() => setBusy(false))
  }

  const bytes = files.reduce((total, file) => total + file.size, 0)

  return (
    <div className="panel p-3">
      <p className="heading">Upload data</p>
      <div
        onDragOver={(event) => {
          event.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault()
          setDragging(false)
          collect(event.dataTransfer.files)
        }}
        onClick={() => inputRef.current?.click()}
        className={`mt-3 cursor-pointer rounded border border-dashed p-5 text-center text-sm ${
          dragging ? 'border-accent text-txt' : 'border-line text-muted'
        }`}
      >
        Drop files here, or click to browse.
        <input
          ref={inputRef}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(event) => collect(event.target.files)}
        />
      </div>
      <p className="mt-2 caption">
        One SQLite file, one .sql dump, or a set of CSV and Excel sheets. Up to 100 MB.
      </p>

      {files.length > 0 && (
        <ul className="mt-3 space-y-0.5 text-sm">
          {files.map((file) => (
            <li key={`${file.name}:${file.size}`} className="flex items-center justify-between gap-3">
              <span className="truncate">{file.name}</span>
              <span className="shrink-0 caption">
                {(file.size / 1024).toFixed(0)} kB{' '}
                <button className="link ml-2" onClick={() => setFiles((current) => current.filter((item) => item !== file))}>
                  remove
                </button>
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex items-center gap-3">
        <button className="btn-primary" onClick={submit} disabled={busy || files.length === 0}>
          {busy ? 'Reading files' : files.length ? `Add ${files.length} file${files.length === 1 ? '' : 's'}` : 'Add files'}
        </button>
        {files.length > 0 && <span className="caption">{(bytes / 1024 / 1024).toFixed(1)} MB selected</span>}
      </div>
    </div>
  )
}
