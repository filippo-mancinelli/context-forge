import { useEffect, useState } from 'react'
import { Loader2, Save, SlidersHorizontal } from 'lucide-react'
import { api } from '../lib/api'

export default function Settings() {
  const [forgeConfigText, setForgeConfigText] = useState('')
  const [settingsText, setSettingsText] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [ok, setOk] = useState<string | null>(null)

  useEffect(() => {
    api.settings.get()
      .then(data => {
        setForgeConfigText(JSON.stringify(data.forge_config, null, 2))
        setSettingsText(JSON.stringify(data.settings_overrides, null, 2))
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const save = async () => {
    setSaving(true)
    setError(null)
    setOk(null)
    try {
      await api.settings.update({
        forge_config: JSON.parse(forgeConfigText),
        settings_overrides: JSON.parse(settingsText),
      })
      setOk('Settings saved and applied.')
    } catch (e) {
      setError(String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="p-8 space-y-6">
      <div>
        <h1 className="text-xl font-semibold text-white flex items-center gap-2">
          <SlidersHorizontal className="w-5 h-5 text-indigo-400" />
          Settings
        </h1>
        <p className="text-sm text-gray-500 mt-1">Manage runtime providers, tokens and repository config from UI.</p>
      </div>

      {loading ? (
        <div className="flex items-center text-gray-500"><Loader2 className="w-4 h-4 animate-spin mr-2" />Loading...</div>
      ) : (
        <>
          {error && <div className="p-3 text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg">{error}</div>}
          {ok && <div className="p-3 text-sm text-emerald-300 bg-emerald-500/10 border border-emerald-500/30 rounded-lg">{ok}</div>}

          <div>
            <p className="text-xs text-gray-500 mb-1">Forge config JSON</p>
            <textarea
              value={forgeConfigText}
              onChange={e => setForgeConfigText(e.target.value)}
              rows={14}
              className="w-full px-3 py-2 text-xs font-mono bg-gray-900 border border-gray-700 rounded-lg"
            />
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">Settings overrides JSON</p>
            <textarea
              value={settingsText}
              onChange={e => setSettingsText(e.target.value)}
              rows={14}
              className="w-full px-3 py-2 text-xs font-mono bg-gray-900 border border-gray-700 rounded-lg"
            />
          </div>

          <button
            onClick={save}
            disabled={saving}
            className="px-4 py-2 text-sm text-white bg-indigo-600 hover:bg-indigo-500 rounded-lg inline-flex items-center gap-2 disabled:opacity-50"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save settings
          </button>
        </>
      )}
    </div>
  )
}

