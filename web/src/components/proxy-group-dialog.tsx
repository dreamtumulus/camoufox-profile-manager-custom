'use client'

import { useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { proxiesAPI, type LibraryProxy } from '@/lib/api'

export function ProxyGroupDialog({ proxies, groups, onClose, onSaved }: {
  proxies: LibraryProxy[]
  groups: string[]
  onClose: () => void
  onSaved: (proxy: LibraryProxy) => void
}) {
  const { t } = useI18n()
  const [group, setGroup] = useState(proxies.length === 1 ? proxies[0].group_name ?? '' : '')
  const [busy, setBusy] = useState(false)
  const [updated, setUpdated] = useState<string[]>([])
  const [failed, setFailed] = useState<{ id: string; reason: string }[]>([])
  const [started, setStarted] = useState(false)

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (busy || started) return
    setBusy(true)
    setStarted(true)
    const succeeded: string[] = []
    const errors: { id: string; reason: string }[] = []
    for (const proxy of proxies) {
      try {
        const saved = await proxiesAPI.update(proxy.id, { group_name: group.trim() || null, row_version: proxy.row_version })
        succeeded.push(proxy.id)
        onSaved(saved)
      } catch (error) {
        errors.push({ id: proxy.id, reason: error instanceof Error ? error.message : String(error) })
      }
    }
    setUpdated(succeeded)
    setFailed(errors)
    setBusy(false)
  }

  return <Modal open title={t('Change proxy group')} width={520} onClose={() => { if (!busy) onClose() }} footer={<>
    <button className="btn btn-default" disabled={busy} onClick={onClose}>{started ? t('Done') : t('Cancel')}</button>
    {!started && <button type="submit" form="proxy-group-form" className="btn btn-primary" disabled={busy || !proxies.length}>{busy ? t('Saving…') : t('Apply group')}</button>}
  </>}>
    {!started ? <form id="proxy-group-form" onSubmit={save} className="space-y-4">
      <p className="text-ink-dim">{t('Change only the library group of {count} selected proxies. Proxies already copied into profiles are unchanged.', { count: proxies.length })}</p>
      <div><label className="field-label" htmlFor="bulk-proxy-group">{t('Destination group')}</label><input id="bulk-proxy-group" className="field" list="bulk-proxy-group-list" value={group} onChange={event => setGroup(event.target.value)} maxLength={64} placeholder={t('Ungrouped')} data-modal-autofocus /><datalist id="bulk-proxy-group-list">{groups.map(item => <option key={item} value={item} />)}</datalist></div>
    </form> : <div role={failed.length ? 'alert' : 'status'}>
      <p>{t('Updated group for {count} proxies.', { count: updated.length })}</p>
      {failed.length > 0 && <><p className="mt-3 text-danger">{t('Proxies skipped: {count}', { count: failed.length })}</p><ul className="mt-2 max-h-[180px] space-y-1 overflow-y-auto text-ink-dim">{failed.map(item => <li key={item.id}>{proxies.find(proxy => proxy.id === item.id)?.name ?? item.id}: {t(item.reason)}</li>)}</ul><p className="mt-2 text-ink-dim">{t('No failed proxy was retried. Reload the library before changing it again.')}</p></>}
    </div>}
  </Modal>
}
