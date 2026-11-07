'use client'

import { useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { profilesAPI, type BulkTagsResult, type Group, type Profile } from '@/lib/api'

export function ProfileGroupDialog({ profiles, groups, onClose, onUpdated }: {
  profiles: Profile[]
  groups: Group[]
  onClose: () => void
  onUpdated: (updated: Profile[]) => void
}) {
  const { t } = useI18n()
  const [group, setGroup] = useState<string>(profiles.length === 1 ? profiles[0].group ?? '' : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<BulkTagsResult | null>(null)

  async function save(event: React.FormEvent) {
    event.preventDefault()
    if (busy || profiles.length > 1000) return
    setBusy(true)
    setError(null)
    try {
      const response = await profilesAPI.bulkGroup(profiles, group || null)
      setResult(response)
      onUpdated(response.updated)
    } catch (err) {
      setError(t(err instanceof Error ? err.message : String(err)))
    } finally {
      setBusy(false)
    }
  }

  return <Modal open title={t('Change profile group')} width={520} onClose={() => { if (!busy) onClose() }} footer={<>
    <button className="btn btn-default" disabled={busy} onClick={onClose}>{result ? t('Done') : t('Cancel')}</button>
    {!result && <button type="submit" form="bulk-group-form" className="btn btn-primary" disabled={busy || profiles.length > 1000}>{busy ? t('Saving…') : t('Apply group')}</button>}
  </>}>
    {result ? <div>
      <p role="status">{t('Updated group for {count} profiles.', { count: result.updated.length })}</p>
      {result.skipped.length > 0 && <div className="mt-3" role="alert"><p className="text-danger">{t('Profiles skipped: {count}', { count: result.skipped.length })}</p><ul className="mt-2 space-y-1 text-ink-dim">{result.skipped.map(item => <li key={item.id}>{profiles.find(profile => profile.id === item.id)?.name ?? item.id}: {t(item.reason)}</li>)}</ul></div>}
    </div> : <form id="bulk-group-form" onSubmit={save} className="space-y-4">
      <p className="text-ink-dim">{t('Change only the group of the selected profiles. Browser data, cookies, fingerprints and proxy settings are preserved.')}</p>
      <p>{t('{count} selected', { count: profiles.length })}</p>
      <div><label className="field-label" htmlFor="bulk-profile-group">{t('Destination group')}</label><select id="bulk-profile-group" className="field" value={group} onChange={event => setGroup(event.target.value)} data-modal-autofocus><option value="">{t('Ungrouped')}</option>{groups.map(item => <option key={item.id} value={item.id}>{item.name} ({item.profile_count})</option>)}</select></div>
      {profiles.length > 1000 && <p className="text-danger" role="alert">{t('Select at most 1000 profiles for one group edit.')}</p>}
    </form>}
    {error && <p className="mt-3 text-danger" role="alert">{error}</p>}
  </Modal>
}
