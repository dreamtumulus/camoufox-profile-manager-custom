'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import { useToast } from '@/components/toast'
import { ApiRequestError, formatProxyString, profilesAPI, type Profile, type ProxyCheckRecord } from '@/lib/api'
import { BatchQueue, MAX_BATCH_SIZE, type BatchSnapshot } from '@/lib/batch-queue'
import { copyText } from '@/lib/clipboard'
import { assignEmails, emailKey, MAX_EMAIL_IMPORT, parseEmailLines, type EmailHolder, type EmailProblem } from '@/lib/email-import'

type Step = 'paste' | 'checking' | 'preview' | 'result'
type Outcome = { updated: Profile[]; skipped: { id: string; reason: string }[]; unused: string[] }

/**
 * Paste addresses, check the target profiles' proxies, confirm the pairs, write.
 *
 * Nothing is written before the confirmation, and the server repeats the rule on
 * its own: an address lands only on a profile without one, created since email
 * addresses were introduced, whose proxy has just passed a check.
 */
export function ProfileEmailImport({ targets, scope, known, withEmail, older, onClose, onChecked, onImported }: {
  /** Profiles that may receive an address and have none, in list order. */
  targets: Profile[]
  scope: 'selected' | 'list'
  /** Every loaded profile, for telling at once that an address is taken. */
  known: Profile[]
  /** How many profiles in scope were left out because they already have an address. */
  withEmail: number
  /** How many were left out because they existed before email addresses did. */
  older: number
  onClose: () => void
  onChecked: (id: string, record: ProxyCheckRecord) => void
  onImported: (updated: Profile[]) => void
}) {
  const { t } = useI18n()
  const toast = useToast()
  const used = useMemo(() => targets.slice(0, MAX_BATCH_SIZE), [targets])
  const [step, setStep] = useState<Step>('paste')
  const [text, setText] = useState('')
  const [serverHolders, setServerHolders] = useState<Map<string, EmailHolder>>(new Map())
  const [emails, setEmails] = useState<string[]>([])
  const [checks, setChecks] = useState<Record<string, ProxyCheckRecord>>({})
  const [snapshot, setSnapshot] = useState<BatchSnapshot | null>(null)
  const [outcome, setOutcome] = useState<Outcome | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const queue = useRef<BatchQueue | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const denied = () => queue.current?.loseAccess()
    window.addEventListener('camoufox-access-denied', denied)
    return () => {
      mounted.current = false
      window.removeEventListener('camoufox-access-denied', denied)
      queue.current?.dispose()
    }
  }, [])

  const holders = useMemo(() => {
    const map = new Map<string, EmailHolder>()
    for (const profile of known) if (profile.email) map.set(emailKey(profile.email), { name: profile.name, inTrash: false })
    for (const [key, holder] of serverHolders) map.set(key, holder)
    return map
  }, [known, serverHolders])
  const parsed = useMemo(() => parseEmailLines(text, holders), [text, holders])
  const tooMany = parsed.emails.length > MAX_EMAIL_IMPORT

  const passed = useMemo(() => new Set(used.filter(profile => profile.proxy_config && checks[profile.id]?.reachable).map(profile => profile.id)), [used, checks])
  const plan = useMemo(() => assignEmails(used.map(profile => profile.id), passed, emails), [used, passed, emails])
  const rows = snapshot?.rows ?? []
  const completed = rows.filter(row => !['waiting', 'running'].includes(row.status)).length

  function problemText(problem: EmailProblem) {
    switch (problem.reason) {
      case 'invalid': return t('Line {line}: not a valid email address', { line: problem.line })
      case 'repeated': return t('Line {line}: repeats an earlier line', { line: problem.line })
      case 'taken': return problem.inTrash
        ? t('Line {line}: already on profile "{name}", which is in Trash', { line: problem.line, name: problem.holder })
        : t('Line {line}: already on profile "{name}"', { line: problem.line, name: problem.holder })
    }
  }

  /** What the check said about one target, for both lists. */
  function checkState(profile: Profile): { text: string; tone: 'ok' | 'danger' | 'dim' | 'busy' } {
    if (!profile.proxy_config) return { text: t('No proxy configured'), tone: 'danger' }
    const row = rows.find(item => item.id === profile.id)
    const check = checks[profile.id]
    if (check) return check.reachable ? { text: t('Proxy reachable'), tone: 'ok' } : { text: t('Proxy not reachable'), tone: 'danger' }
    if (row?.status === 'running') return { text: t('Checking…'), tone: 'busy' }
    if (row?.status === 'failed') return { text: t('Could not check proxy'), tone: 'danger' }
    if (row?.status === 'cancelled') return { text: t('Not checked'), tone: 'dim' }
    return { text: t('Waiting'), tone: 'dim' }
  }
  const toneClass = { ok: 'text-ok', danger: 'text-danger', dim: 'text-ink-faint', busy: 'text-signal' }

  async function start() {
    if (busy || !parsed.emails.length || tooMany || !used.length) return
    setBusy(true); setError(null)
    try {
      // The list only knows live profiles this session loaded; the server also
      // knows the ones in Trash.
      const taken = await profilesAPI.takenEmails(parsed.emails)
      if (!mounted.current) return
      if (taken.length) {
        setServerHolders(current => {
          const next = new Map(current)
          for (const item of taken) next.set(emailKey(item.email), { name: item.profile_name, inTrash: item.in_trash })
          return next
        })
        setError(t('Some addresses are already on a profile and were left out. Review the lines below, then continue.'))
        return
      }
    } catch {
      if (mounted.current) setError(t('Could not check which addresses are already in use. Try again.'))
      return
    } finally { if (mounted.current) setBusy(false) }

    const wanted = parsed.emails
    setEmails(wanted); setChecks({}); setSnapshot(null); setStep('checking')
    const proxied = used.filter(profile => profile.proxy_config)
    if (!proxied.length) { setStep('preview'); return }
    let reachable = 0
    const batch = new BatchQueue(proxied.map(({ id, name }) => ({ id, name })), {
      concurrency: Math.min(4, proxied.length),
      execute: async id => {
        const result = await profilesAPI.checkProxy(id)
        const record: ProxyCheckRecord = {
          checked_at: result.checked_at ?? new Date().toISOString(),
          reachable: result.reachable,
          error: result.error,
          latency_ms: result.latency_ms,
          ip: result.location?.ip ?? null,
          country: result.location?.country ?? null,
          timezone: result.location?.timezone ?? null,
          findings: result.findings,
        }
        // A check that finishes after the dialog closed still belongs on the list.
        onChecked(id, record)
        if (mounted.current) setChecks(current => ({ ...current, [id]: record }))
        // Enough profiles passed for every address: the rest need no check.
        if (record.reachable && ++reachable >= wanted.length) batch.cancelWaiting()
      },
      onChange: value => { if (mounted.current) setSnapshot(value) },
      accessDenied: failure => failure instanceof ApiRequestError && (failure.status === 401 || failure.status === 403),
      // Never retain upstream errors: they can contain proxy credentials.
      describeError: () => 'Could not check proxy',
    })
    queue.current = batch
    await batch.run()
    if (mounted.current && queue.current === batch) setStep('preview')
  }

  function backToPaste() {
    queue.current?.dispose()
    queue.current = null
    setSnapshot(null); setChecks({}); setError(null); setStep('paste')
  }

  async function confirm() {
    if (busy || !plan.assigned.size) return
    setBusy(true); setError(null)
    try {
      const assignments = used.filter(profile => plan.assigned.has(profile.id)).map(profile => ({ id: profile.id, row_version: profile.row_version, email: plan.assigned.get(profile.id)! }))
      const result = await profilesAPI.importEmails(assignments)
      onImported(result.updated)
      if (!mounted.current) return
      const refused = new Set(result.skipped.map(item => item.id))
      setOutcome({ ...result, unused: [...assignments.filter(item => refused.has(item.id)).map(item => item.email), ...plan.unused] })
      setStep('result')
    } catch (failure) {
      if (mounted.current) setError(failure instanceof ApiRequestError && (failure.status === 401 || failure.status === 403)
        ? t('Administrator access is required. Sign in again before continuing.')
        : t('The import could not be confirmed. Reload the list to see which profiles received an address before importing again.'))
    } finally { if (mounted.current) setBusy(false) }
  }

  function close() {
    if (busy) return
    queue.current?.cancelWaiting()
    onClose()
  }

  async function copyUnused(values: string[]) {
    if (await copyText(values.join('\n'))) toast('ok', t('Addresses copied'))
    else toast('error', t('Could not copy. Select the addresses and copy them by hand.'))
  }

  const nameOf = (id: string) => used.find(profile => profile.id === id)?.name ?? id
  const stepIndex = step === 'paste' ? 0 : step === 'checking' ? 1 : 2
  const stepNames = [t('Paste addresses'), t('Check proxies'), t('Confirm import')]

  const mapping = (done: boolean) => <ul className="max-h-[300px] divide-y divide-line overflow-y-auto rounded-md border border-line" aria-label={t('Profiles and the email address each receives')}>
    {used.map(profile => {
      const state = checkState(profile)
      const email = done ? outcome?.updated.find(item => item.id === profile.id)?.email : plan.assigned.get(profile.id)
      const refusal = done ? outcome?.skipped.find(item => item.id === profile.id)?.reason : undefined
      return <li key={profile.id} className="grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)_minmax(0,1.2fr)] items-center gap-3 px-3 py-2">
        <span className="min-w-0"><span className="block truncate font-medium">{profile.name}</span><span className="block truncate font-mono text-[11px] text-ink-faint">{formatProxyString(profile.proxy_config) || '—'}</span></span>
        <span className={`min-w-0 truncate ${toneClass[state.tone]}`}>{state.text}</span>
        <span className="min-w-0 truncate font-mono">{email ?? <span className="font-sans text-ink-faint">{refusal ? t(refusal) : passed.has(profile.id) ? t('No address left for it') : t('Skipped')}</span>}</span>
      </li>
    })}
  </ul>

  const leftover = (values: string[]) => values.length > 0 && <div className="rounded-md border border-line bg-canvas px-3 py-2.5">
    <div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{t('Addresses left over: {count}', { count: values.length })}</p><button type="button" className="btn btn-default h-7" onClick={() => copyUnused(values)}>{t('Copy addresses')}</button></div>
    <p className="mt-1 max-h-[90px] overflow-y-auto whitespace-pre-wrap break-all font-mono text-ink-dim">{values.join('\n')}</p>
    <p className="mt-1 text-ink-dim">{t('They were not recorded and can be imported again.')}</p>
  </div>

  return <Modal open title={t('Import emails')} width={760} onClose={close} footer={<div className="flex min-w-0 flex-wrap items-center justify-end gap-2">
    {step === 'checking' && <span className="mr-auto text-ink-dim">{t('Cancelling writes no email address.')}</span>}
    <button className="btn btn-default" disabled={busy} onClick={close}>{step === 'result' ? t('Done') : step === 'checking' ? t('Cancel checks') : t('Cancel')}</button>
    {step === 'preview' && <button className="btn btn-default" disabled={busy} onClick={backToPaste}>{t('Back')}</button>}
    {step === 'paste' && <button className="btn btn-primary" disabled={busy || !parsed.emails.length || tooMany || !used.length} onClick={start}>{busy ? t('Checking…') : t('Check proxies and preview')}</button>}
    {step === 'checking' && <button className="btn btn-primary" disabled>{t('Checking…')}</button>}
    {step === 'preview' && <button className="btn btn-primary" disabled={busy || !plan.assigned.size} onClick={confirm}>{busy ? t('Importing…') : plan.assigned.size === 1 ? t('Import 1 email address') : t('Import {count} email addresses', { count: plan.assigned.size })}</button>}
  </div>}>
    {step !== 'result' && <ol className="mb-4 flex flex-wrap items-center gap-2 text-[12px] text-ink-faint">
      {stepNames.map((name, index) => <li key={name} aria-current={index === stepIndex ? 'step' : undefined} className={`rounded-full border px-2.5 py-0.5 ${index === stepIndex ? 'border-signal bg-signal-shade text-signal-soft' : index < stepIndex ? 'border-line text-ink-dim' : 'border-line'}`}>{index + 1}. {name}</li>)}
    </ol>}

    {step === 'paste' && <div className="flex flex-col gap-3">
      <div className="rounded-md border border-line bg-canvas px-3 py-2.5">
        {used.length === 0
          ? <p role="alert" className="text-danger">{scope === 'selected' ? t('No selected profile can receive an email address.') : t('No profile in the current list can receive an email address.')}</p>
          : <>
            <p>{scope === 'selected'
              ? t('Addresses go to the selected profiles without an email address ({count}), in list order, one address each.', { count: used.length })
              : t('Addresses go to the profiles in the current list without an email address ({count}), in list order, one address each.', { count: used.length })}</p>
            <p className="mt-1 text-ink-dim">{t('To import to particular profiles only, close this dialog and select them first.')}</p>
          </>}
        {withEmail > 0 && <p className="mt-1 text-ink-dim">{t('Profiles that already have an email address are never changed: {count}.', { count: withEmail })}</p>}
        {older > 0 && <p className="mt-1 text-ink-dim">{t('Profiles created before this version cannot receive an email address: {count}.', { count: older })}</p>}
        {targets.length > used.length && <p className="mt-1 text-ink-dim">{t('One import covers the first {count} profiles.', { count: used.length })}</p>}
      </div>
      <label htmlFor="email-import-text" className="font-medium">{t('One email address per line (up to 100)')}</label>
      <textarea id="email-import-text" className="field resize-y font-mono" rows={9} maxLength={40000} value={text} disabled={busy} onChange={event => { setText(event.target.value); setError(null) }} spellCheck={false} autoComplete="off" aria-describedby="email-import-count email-import-help" data-modal-autofocus />
      <p id="email-import-count" role="status" className={tooMany ? 'text-danger' : 'text-ink-dim'}>{t('Lines: {lines}; usable: {usable}; cannot be imported: {refused}.', { lines: parsed.lines, usable: parsed.emails.length, refused: parsed.problems.length })}</p>
      {tooMany && <p role="alert" className="text-danger">{t('Paste at most {count} email addresses at a time.', { count: MAX_EMAIL_IMPORT })}</p>}
      {parsed.problems.length > 0 && <ul className="max-h-[120px] space-y-0.5 overflow-y-auto text-danger" aria-label={t('Lines that cannot be imported')}>{parsed.problems.map(problem => <li key={problem.line}>{problemText(problem)}</li>)}</ul>}
      <p id="email-import-help" className="text-ink-dim">{t('An address is written only to a profile whose proxy passes a check. A profile that does not pass is skipped, and its address goes to the next profile that passes.')}</p>
    </div>}

    {step === 'checking' && <div className="flex flex-col gap-3">
      <p>{t('Checking the proxies of the target profiles. No email address has been written yet.')}</p>
      <progress className="w-full accent-signal" aria-label={t('Batch progress')} max={Math.max(rows.length, 1)} value={completed} />
      <p role="status" className="text-ink-dim">{t('{completed} of {total} processed', { completed, total: rows.length })}</p>
      {snapshot?.accessLost && <p role="alert" className="text-danger">{t('Access changed. Remaining requests were cancelled. Reopen the queue after signing in and checking permissions.')}</p>}
      {mapping(false)}
    </div>}

    {step === 'preview' && <div className="flex flex-col gap-3">
      <p>{t('Proxy checks finished. No email address has been written yet. Confirm the pairs below.')}</p>
      <p role="status" className="text-ink-dim">{t('Profiles to receive an address: {assigned}; profiles skipped: {skipped}; addresses left over: {unused}.', { assigned: plan.assigned.size, skipped: used.length - plan.assigned.size, unused: plan.unused.length })}</p>
      {snapshot?.accessLost && <p role="alert" className="text-danger">{t('Access changed. Remaining requests were cancelled. Reopen the queue after signing in and checking permissions.')}</p>}
      {!plan.assigned.size && <p role="alert" className="text-danger">{t('No profile passed the proxy check, so nothing can be imported.')}</p>}
      {mapping(false)}
      {leftover(plan.unused)}
    </div>}

    {step === 'result' && outcome && <div className="flex flex-col gap-3">
      <p role="status" className={outcome.updated.length ? 'font-medium text-ok' : 'font-medium'}>{t('Email addresses written: {count}.', { count: outcome.updated.length })}</p>
      {outcome.skipped.length > 0 && <div role="alert"><p className="text-danger">{t('Not written after all: {count}', { count: outcome.skipped.length })}</p><ul className="mt-1 space-y-0.5 text-ink-dim">{outcome.skipped.map(item => <li key={item.id}>{nameOf(item.id)}: {t(item.reason)}</li>)}</ul></div>}
      {mapping(true)}
      {leftover(outcome.unused)}
    </div>}

    {error && <p role="alert" className="mt-3 break-words text-danger">{error}</p>}
  </Modal>
}
