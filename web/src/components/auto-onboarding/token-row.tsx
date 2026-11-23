'use client'

import { useState } from 'react'

import { useI18n } from '@/components/i18n-provider'
import type { Connection } from '@/lib/auto-onboarding-api'
import { TOKEN_PREFIX, tokenProblem } from '@/lib/auto-onboarding-preset'

/**
 * The onboarder token: typed once, then shown only as a masked preview.
 *
 * A new token takes the saved one's place only after the platform accepts it,
 * so cancelling, a typo or a refusal leaves the saved one in use.
 */
export function TokenRow({
  connection,
  counts,
  busy,
  notice,
  onSave,
  onRefresh,
  onEditing,
}: {
  connection: Connection
  /** How many nodes and organization profiles the token reaches, once read. */
  counts: { nodes: number; orgs: number } | null
  busy: 'read' | 'refresh' | null
  /** What the last refresh or replacement changed. */
  notice: { tone: 'info' | 'warn'; text: string } | null
  /** Resolves to what the platform said when it refused, or null once saved. */
  onSave: (token: string) => Promise<string | null>
  onRefresh: () => void
  onEditing: (editing: boolean) => void
}) {
  const { t, locale } = useI18n()
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const [shown, setShown] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const form = editing || !connection.saved

  function close() {
    setEditing(false)
    setValue('')
    setShown(false)
    setError(null)
    onEditing(false)
  }

  async function save() {
    const problem = tokenProblem(value)
    if (problem) {
      setError(problem === 'empty' ? t('Enter the onboarder token.') : t('This does not look like an onboarder token: it starts with {prefix}.', { prefix: TOKEN_PREFIX }))
      return
    }
    setError(null)
    const refusal = await onSave(value.trim())
    if (refusal) setError(refusal)
    else close()
  }

  const at = (stamp: string | null) => (stamp ? new Date(stamp).toLocaleTimeString(locale) : '')
  const status = busy === 'refresh' ? t('Refreshing…')
    : busy === 'read' ? t('Reading…')
    : connection.refused ? t('The platform no longer accepts this token (refused at {time}). Change it to a valid one to check or go on.', { time: at(connection.refused_at) })
    : connection.saved ? t('Read · token “{label}” · nodes {nodes} · organization profiles {orgs} · {time}', { label: connection.label ?? '', nodes: counts?.nodes ?? 0, orgs: counts?.orgs ?? 0, time: at(connection.read_at) })
    : t('No token saved yet. Save one to choose a node and an organization.')

  return (
    <div className="border-b border-line px-4 py-3">
      <div className="field-label" id="onboard-token-label">{t('Onboarder token')}</div>
      {form ? (
        <div className="flex flex-wrap gap-2">
          <input
            className="field min-w-[150px] flex-1 font-mono"
            type={shown ? 'text' : 'password'}
            autoComplete="off"
            spellCheck={false}
            placeholder={`${TOKEN_PREFIX}…`}
            aria-labelledby="onboard-token-label"
            value={value}
            disabled={busy !== null}
            onChange={event => { setValue(event.target.value); setError(null) }}
            onKeyDown={event => {
              if (event.key === 'Enter') { event.preventDefault(); save() }
              else if (event.key === 'Escape' && connection.saved) { event.preventDefault(); close() }
            }}
          />
          <button type="button" className="btn btn-ghost" onClick={() => setShown(!shown)} aria-pressed={shown}>
            {shown ? t('Hide') : t('Show')}
          </button>
          <button type="button" className="btn btn-primary" disabled={busy !== null} onClick={save}>{t('Save and read')}</button>
          {connection.saved && <button type="button" className="btn btn-default" disabled={busy !== null} onClick={close}>{t('Cancel')}</button>}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <div className="min-w-[150px] flex-1 rounded-md border border-dashed border-line px-2.5 py-1.5 font-mono" data-testid="token-preview">{connection.preview}</div>
          <button type="button" className="btn btn-default" disabled={busy !== null} onClick={onRefresh}>{busy === 'refresh' ? t('Refreshing…') : t('Refresh')}</button>
          <button type="button" className="btn btn-default" disabled={busy !== null} onClick={() => { setEditing(true); onEditing(true) }}>{t('Change')}</button>
        </div>
      )}
      {error && <p className="mt-1.5 text-[12px] text-danger" role="alert">{error}</p>}
      <p className={`mt-1.5 text-[12px] ${connection.refused && !busy ? 'text-danger' : 'text-ink-dim'}`} role="status" data-testid="token-status">{status}</p>
      {form ? (
        <p className="mt-1 text-[12px] text-ink-dim">
          {connection.saved
            ? t('The new token replaces the saved one only after the platform accepts it; Cancel keeps the saved one.')
            : t('The token is kept on this computer and is not shown in full again once saved.')}
        </p>
      ) : notice && (
        <p className={`mt-1 text-[12px] ${notice.tone === 'warn' ? 'text-warn' : 'text-ink-dim'}`} role="status" data-testid="token-notice">{notice.text}</p>
      )}
      <p className="mt-1 text-[12px] text-ink-dim">{t('Platform {url} · the token is kept on this computer and is sent only to this address.', { url: connection.platform })}</p>
      {connection.saved && connection.encrypted === false && (
        <p className="mt-1 text-[12px] text-warn">{t('This installation has no encryption key (CPM_SECRET_KEY), so the token is kept unencrypted, as proxy passwords are.')}</p>
      )}
    </div>
  )
}
