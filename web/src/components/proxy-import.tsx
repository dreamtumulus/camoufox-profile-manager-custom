'use client'

import { useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { ApiRequestError, proxiesAPI, type ProxyImportResult } from '@/lib/api'

export function ProxyImport({ groups = [], onClose, onImported }: { groups?: string[]; onClose: () => void; onImported: () => void }) {
  const { t } = useI18n()
  const [text, setText] = useState('')
  const [group, setGroup] = useState('')
  // The group the finished import was filed under, kept apart from the input
  // so the result names what was actually sent.
  const [importedGroup, setImportedGroup] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<ProxyImportResult | null>(null)
  const lines = text.split(/\r?\n/).filter(line => line.trim()).length
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (busy || !lines || lines > 100) return
    setBusy(true); setError(null)
    try {
      const groupName = group.trim() || null
      const response = await proxiesAPI.importText(text, groupName)
      setText('')
      setImportedGroup(groupName)
      setResult(response)
      onImported()
    } catch (err) {
      // Import errors never echo a pasted URL or the credentials it may contain.
      const line = err instanceof Error ? err.message.match(/\bLine (\d+)\b/i)?.[1] : undefined
      setError(line ? t('Import failed on line {line}. Check its proxy format and try again.', { line }) : err instanceof ApiRequestError && err.status === 409 ? t('Import requires a working encryption key for password-protected proxies.') : t('Import failed. Check the proxy format on each line and try again. No credentials are displayed here.'))
    } finally { setBusy(false) }
  }
  return <Modal open title={t('Import proxies')} onClose={() => { if (!busy) { setText(''); onClose() } }} width={620} footer={<>
    <button className="btn btn-default" disabled={busy} onClick={() => { setText(''); onClose() }}>{result ? t('Done') : t('Cancel')}</button>
    {!result && <button type="submit" form="proxy-import" className="btn btn-primary" disabled={busy || !lines || lines > 100}>{busy ? t('Importing…') : t('Import proxies')}</button>}
  </>}>
    {result ? <div><p role="status">{result.created.length === 1 ? t('1 proxy imported.') : t('{count} proxies imported.', { count: result.created.length })}</p>{importedGroup && result.created.length > 0 && <p className="mt-2">{t('Added to group: {group}', { group: importedGroup })}</p>}{(result.also_in_other_groups ?? 0) > 0 && <p className="mt-2 text-warn">{t('Also saved in another group: {count}. Profiles created from both groups would share these proxies.', { count: result.also_in_other_groups ?? 0 })}</p>}{result.skipped.length > 0 && <div className="mt-3"><p className="font-medium">{t('Skipped lines')}</p><ul className="mt-2 space-y-1 text-ink-dim">{result.skipped.map(item => <li key={item.line}>{t('Line {line}: {reason}', { line: item.line, reason: t(item.reason) })}</li>)}</ul></div>}<p className="mt-3 text-ink-dim">{t('Import text has been cleared. Check each proxy on demand before assigning it.')}</p></div> : <form id="proxy-import" onSubmit={submit} className="flex flex-col gap-3">
      <div className="flex flex-col gap-1.5 pb-1">
        <label htmlFor="proxy-import-group" className="font-medium">{t('Proxy group (optional)')}</label>
        <input id="proxy-import-group" className="field" list="proxy-import-group-list" value={group} onChange={event => setGroup(event.target.value)} maxLength={64} placeholder={t('Ungrouped')} autoComplete="off" aria-describedby="proxy-import-group-help" />
        <datalist id="proxy-import-group-list">{groups.map(item => <option key={item} value={item} />)}</datalist>
        <p id="proxy-import-group-help" className="text-ink-dim">{t('Every proxy in this import goes into this group. Pick an existing group or type a new name; leave it empty to keep them ungrouped.')} {t('A proxy another group already has is imported again into this one; only a proxy this group already has is skipped.')}</p>
      </div>
      <label htmlFor="proxy-import-text" className="font-medium">{t('One proxy per line (up to 100)')}</label>
      <p id="proxy-import-help" className="text-ink-dim">{t('Use http://user:password@host:port, https://host:port or socks5://host:port. Bare host:port uses HTTP; IPv6 addresses need brackets.')}</p>
      <textarea id="proxy-import-text" className="field resize-y font-mono" rows={8} maxLength={64000} value={text} onChange={event => setText(event.target.value)} spellCheck={false} autoComplete="off" aria-describedby="proxy-import-help proxy-import-count" data-modal-autofocus />
      <p className="text-ink-dim">{t('{count} / 64000 characters', { count: text.length })}</p>
      <p id="proxy-import-count" className={lines > 100 ? 'text-danger' : 'text-ink-dim'} role="status">{t('{count} / 100 lines', { count: lines })}</p>
      <p className="text-ink-dim">{t('Credentials may be visible in this input. The text is cleared when the dialog closes or import completes.')}</p>
      {error && <p className="text-danger" role="alert">{error}</p>}
    </form>}
  </Modal>
}
