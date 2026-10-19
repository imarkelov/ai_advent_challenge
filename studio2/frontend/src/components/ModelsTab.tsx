import { useEffect, useState } from 'react'
import { getConfig, saveConfig, testModel, type StudioConfig } from '../api'

export default function ModelsTab() {
  const [config, setConfig] = useState<StudioConfig | null>(null)
  const [error, setError] = useState('')
  const [checks, setChecks] = useState<Record<string, boolean>>({})

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((exc: Error) => setError(exc.message))
  }, [])

  const update = async (role: string, field: 'provider' | 'model', value: string) => {
    if (!config) return
    const next = { ...config, roles: { ...config.roles, [role]: { ...config.roles[role], [field]: value } } }
    setConfig(next)
    try {
      setConfig(await saveConfig({ roles: { [role]: { [field]: value } } }))
      setError('')
    } catch (exc) {
      setError((exc as Error).message)
    }
  }

  const check = async (role: string) => {
    const result = await testModel(role)
    setChecks((prev) => ({ ...prev, [role]: result.ok }))
  }

  if (!config) return <div className="hint">{error || 'загрузка…'}</div>

  return (
    <div>
      <h2>Модели по ролям</h2>
      {error && <div className="notice err">{error}</div>}
      <table>
        <thead>
          <tr><th>Роль</th><th>Провайдер</th><th>Модель</th><th>Статус</th><th /></tr>
        </thead>
        <tbody>
          {Object.entries(config.roles).map(([role, spec]) => (
            <tr key={role}>
              <td>{role}</td>
              <td>
                <select value={spec.provider} onChange={(event) => update(role, 'provider', event.target.value)}>
                  {Object.keys(config.providers).map((name) => (
                    <option key={name} value={name}>{name}</option>
                  ))}
                </select>
              </td>
              <td>
                <input value={spec.model} onChange={(event) => update(role, 'model', event.target.value)} />
              </td>
              <td>{checks[role] === undefined ? '—' : checks[role] ? 'ok' : 'ошибка'}</td>
              <td><button onClick={() => check(role)}>Проверить</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 style={{ marginTop: 24 }}>Провайдеры</h2>
      <table>
        <thead><tr><th>Провайдер</th><th>base_url</th><th>Ключ</th></tr></thead>
        <tbody>
          {Object.entries(config.providers).map(([name, provider]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{provider.base_url ?? '—'}</td>
              <td>{provider.api_key_masked || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
