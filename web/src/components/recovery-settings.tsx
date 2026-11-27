'use client'

import { useState } from 'react'
import { Download, ExternalLink, LoaderCircle } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { VerifiedUpdateSettings } from '@/components/verified-update-settings'
import { systemAPI, type AppUpdate } from '@/lib/api'

function message(error: unknown): string { return error instanceof Error ? error.message : String(error) }

/** The download is exposed only after the server has confirmed the release. */
export function UpdateSettings() {
  const { t } = useI18n()
  const [update, setUpdate] = useState<AppUpdate | null>(null)
  const [checking, setChecking] = useState(false)
  const [preparing, setPreparing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [prepared, setPrepared] = useState<string | null>(null)
  const [preview, setPreview] = useState(false)
  async function check() {
    setChecking(true); setError(null); setPrepared(null)
    try {
      const result = await systemAPI.updates(preview)
      setUpdate(result)
      if (result.error) setError(result.error)
    } catch (err) { setError(message(err)) }
    finally { setChecking(false) }
  }
  async function prepare() {
    setPreparing(true); setError(null)
    try {
      const result = await systemAPI.prepareUpdate(preview)
      if (!isReleaseLink(result.release_url)) throw new Error(t('The server returned an unverified release link. Check for updates again.'))
      setPrepared(result.release_url)
    } catch (err) { setError(message(err)) }
    finally { setPreparing(false) }
  }

  return <section aria-labelledby="updates-title">
    <h2 id="updates-title" className="mb-2 text-[11px] font-semibold uppercase tracking-[0.08em] text-ink-faint">{t('App updates')}</h2>
    <div className="panel p-4">
      <p className="font-medium">{t('Check for a new desktop release')}</p>
      <p className="mt-1 text-ink-dim">{t('Close all running profiles before updating. Install the downloaded release to update the app.')}</p>
      <label className="mt-3 flex min-h-11 items-center gap-2 text-ink-dim"><input type="checkbox" checked={preview} disabled={checking || preparing} onChange={event => { setPreview(event.target.checked); setUpdate(null); setPrepared(null); setError(null) }} />{t('Include preview releases (opt-in)')}</label>
      {preview && <p className="text-ink-dim">{t('Preview installers may be unsigned and are intended for testing. Stable remains the default channel.')}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button className="btn btn-default" disabled={checking || preparing} onClick={check}>{checking && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{checking ? t('Checking…') : t('Check for updates')}</button>
        {update?.available && !prepared && !error && <button className="btn btn-primary" disabled={preparing || checking} onClick={prepare}>{preparing && <LoaderCircle size={13} className="animate-spin motion-reduce:animate-none" aria-hidden="true" />}{preparing ? t('Preparing…') : t('Prepare update')}</button>}
      </div>
      {update && !error && <p className="mt-3 text-ink-dim" role="status">{update.available ? t('Version {latest} is available. You have {current}.', { latest: update.latest_version, current: update.current_version }) : t('You are up to date ({version}).', { version: update.current_version })}</p>}
      {error && <p className="mt-3 text-danger" role="alert">{t(error)}</p>}
      {prepared && <div className="mt-3 rounded-md border border-line bg-raised p-3">
        <p role="status">{t('Your release is ready to download.')}</p>
        <a className="btn btn-primary mt-3" href={prepared} target="_blank" rel="noopener noreferrer"><Download size={13} aria-hidden="true" />{t('Open release downloads')}<ExternalLink size={12} aria-hidden="true" /></a>
        <p className="mt-2 text-ink-dim">{t('Download the asset for your operating system.')}</p>
      </div>}
      <details className="mt-4 border-t border-line pt-3">
        <summary className="cursor-pointer font-medium">{t('Update recovery')}</summary>
        <p className="mt-2 text-ink-dim">{t('Verified updates create a complete recovery point. Keep your existing data folder when using a manual installer.')}</p>
        <VerifiedUpdateSettings preview={preview} />
      </details>
    </div>
  </section>
}

/** The server verifies the repository; the UI also rejects non-release and unsafe links. */
function isReleaseLink(url: string): boolean {
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'https:' && parsed.hostname === 'github.com' && /^\/polyackiy\/camoufox-profile-manager\/releases\/tag\/[^/]+\/?$/.test(parsed.pathname) && !parsed.username && !parsed.password
  } catch { return false }
}
