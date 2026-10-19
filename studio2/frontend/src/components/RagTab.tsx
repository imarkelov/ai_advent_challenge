import { useCallback, useEffect, useState } from 'react'
import { deleteDoc, getDocTree, readDoc, reindex, uploadDoc, writeDoc, type DocNode } from '../api'

const EDITABLE = ['kb', 'tasks', 'agents', 'templates']

export default function RagTab() {
  const [tree, setTree] = useState<DocNode[]>([])
  const [path, setPath] = useState('')
  const [content, setContent] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setTree((await getDocTree()).tree)
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const open = async (file: string) => {
    setPath(file)
    try {
      setContent((await readDoc(file)).content)
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const save = async () => {
    try {
      await writeDoc(path, content)
      setStatus(`сохранено: ${path}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const upload = async (dir: string, file: File) => {
    try {
      const result = await uploadDoc(dir, file)
      setStatus(`загружено: ${result.path}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const remove = async (file: string) => {
    try {
      await deleteDoc(file)
      setStatus(`удалено: ${file}`)
      await load()
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const doReindex = async () => {
    const result = await reindex()
    setStatus(`проиндексировано документов: ${result.documents}`)
  }

  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}>
        <button onClick={doReindex}>Переиндексировать</button>
        <label className="hint">
          Загрузить в kb
          <input type="file" onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void upload('kb', file)
          }} />
        </label>
        <label className="hint">
          Загрузить в tasks
          <input type="file" onChange={(event) => {
            const file = event.target.files?.[0]
            if (file) void upload('tasks', file)
          }} />
        </label>
      </div>
      {error && <div className="notice err">{error}</div>}
      {status && <div className="notice">{status}</div>}
      <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', gap: 16 }}>
        <div>
          {tree.map((node) => (
            <div key={node.name}>
              <h2>{node.name}</h2>
              {node.children.map((file) => (
                <div key={file} className="row">
                  <button onClick={() => open(file)}>{file}</button>
                  {['kb/', 'tasks/'].some((prefix) => file.startsWith(prefix)) && (
                    <button onClick={() => remove(file)}>×</button>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>
        <div>
          <h2>{path || 'выберите файл'}</h2>
          <textarea value={content} onChange={(event) => setContent(event.target.value)} rows={20} />
          <div className="row" style={{ marginTop: 8 }}>
            <button onClick={save} disabled={!path || !EDITABLE.some((dir) => path.startsWith(`${dir}/`))}>
              Сохранить
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
