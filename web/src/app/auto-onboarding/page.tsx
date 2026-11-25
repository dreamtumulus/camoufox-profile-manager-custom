'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { Play, Square, UserRoundCheck } from 'lucide-react'

import { AuthorizeDialog, TimeLeft, TONES } from '@/components/auto-onboarding/authorize-dialog'
import { PresetSteps } from '@/components/auto-onboarding/preset-card'
import { TokenRow } from '@/components/auto-onboarding/token-row'
import { EmptyState } from '@/components/empty-state'
import { useI18n } from '@/components/i18n-provider'
import { useAuth } from '@/components/login-gate'
import { useToast } from '@/components/toast'
import { ApiRequestError, browsersAPI } from '@/lib/api'
import {
  autoOnboardingAPI,
  type Connection,
  type Options,
  type Preflight,
  type Preset,
  type Presets,
  type RowJob,
  type ScopeGroup,
  type ScopeRow,
} from '@/lib/auto-onboarding-api'
import { authorizationSaid, isOpen, linkBlock, refusalSaid } from '@/lib/auto-onboarding-authorization'
import {
  ISSUE_MESSAGES,
  OPTIONS_ERRORS,
  ROW_MESSAGES,
  exitNote,
  reconcile,
  samePreset,
  scopeOf,
  type Change,
} from '@/lib/auto-onboarding-preset'
import { copyEmailOnClick, launchBrowser, stopBrowser, syncBlocksLaunch } from '@/lib/browser-runs'
import { profileSyncAPI, type ProfileSyncBinding } from '@/lib/sync-api'

type Notice = { tone: 'info' | 'warn'; text: string }
/** What a row is in the middle of: its browser starting or closing, or a link being made. */
type RowBusy = 'run' | 'stop' | 'link'

/**
 * Auto onboarding: the parameters, and the authorization of one row at a time, by hand.
 *
 * The page keeps the onboarder token, what is chosen among what that token may
 * use, and which group to onboard. Every row of that group can have its
 * browser run and stopped, as on the profiles page, and can be given an
 * authorize link: the link is opened in the row's browser, the person signs
 * in and consents there, and pastes back the code the page shows. Nothing is
 * filled in or pressed for them, and there is no batch.
 */
export default function AutoOnboardingPage() {
  const { t, locale } = useI18n()
  const { canManageAccess } = useAuth()
  const toast = useToast()
  const [connection, setConnection] = useState<Connection | null>(null)
  const [options, setOptions] = useState<Options | null>(null)
  const [optionsError, setOptionsError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset | null>(null)
  const [groups, setGroups] = useState<ScopeGroup[]>([])
  const [rows, setRows] = useState<{ group: string; rows: ScopeRow[] } | null>(null)
  const [check, setCheck] = useState<Preflight | null>(null)
  const [checkError, setCheckError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [busy, setBusy] = useState<'read' | 'refresh' | 'check' | null>(null)
  const [running, setRunning] = useState<Set<string>>(new Set())
  const [rowBusy, setRowBusy] = useState<Map<string, RowBusy>>(new Map())
  // Why the last link asked for a row was refused; gone with the row's next action.
  const [rowErrors, setRowErrors] = useState<Map<string, string>>(new Map())
  const [syncBindings, setSyncBindings] = useState<ProfileSyncBinding[]>([])
  const [dialog, setDialog] = useState<{ profileId: string; job: RowJob } | null>(null)
  const [tokenEditing, setTokenEditing] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  // What the server last had or was last sent, so an unchanged preset is not saved again.
  const sent = useRef<Preset | null>(null)
  // Saves go out one after another: the last choice made is the last one written.
  const saves = useRef<Promise<void>>(Promise.resolve())
  const poolRequest = useRef(0)
  // The check now on screen, for the code that reads the rows again.
  const checkRef = useRef<Preflight | null>(null)
  // Which check request is the latest: an answer to an earlier one, or to one
  // asked before the check was dropped, is not shown.
  const checkAsked = useRef(0)
  const checksInFlight = useRef(0)
  // The onboarded rows a renewed check is on its way for, and those it was last renewed for.
  const renewing = useRef('')
  const renewedFor = useRef('')

  const showCheck = useCallback((next: Preflight | null) => {
    checkRef.current = next
    setCheck(next)
  }, [])

  /** Forget the check: what it judged has changed. An answer still on its way is dropped too. */
  const dropCheck = useCallback(() => {
    checkAsked.current += 1
    showCheck(null)
    setCheckError(null)
  }, [showCheck])

  /** Ask the server to check the saved choices. Null when the answer came too late to matter. */
  const askCheck = useCallback(async (): Promise<{ check: Preflight } | { error: unknown } | null> => {
    const asked = ++checkAsked.current
    checksInFlight.current += 1
    setBusy(current => current ?? 'check')
    try {
      await saves.current
      const next = await autoOnboardingAPI.preflight()
      return asked === checkAsked.current ? { check: next } : null
    } catch (error) {
      return asked === checkAsked.current ? { error } : null
    } finally {
      checksInFlight.current -= 1
      if (checksInFlight.current === 0) setBusy(current => (current === 'check' ? null : current))
    }
  }, [])

  /**
   * A check is a picture of one moment. Once a row it judged has been
   * onboarded the picture is wrong: of that row, and maybe of others, whose
   * exit the new account now fills. So the page asks for the check again by
   * itself. With nothing left to onboard, or without an answer, the check is
   * dropped and nothing is said: nobody asked for it.
   */
  const keepCheckTrue = useCallback(async (fresh: ScopeRow[]) => {
    const current = checkRef.current
    if (!current) return
    const judged = new Map(current.rows.map(row => [row.profile_id, row.status]))
    const onboarded = fresh.filter(row => row.onboarded)
    if (!onboarded.some(row => judged.has(row.profile_id) && judged.get(row.profile_id) !== 'onboarded')) return
    const key = onboarded.map(row => row.profile_id).sort().join(',')
    if (renewing.current === key) return
    // Nothing is left to onboard; or the check was renewed for these rows and still says otherwise.
    if (onboarded.length === fresh.length || renewedFor.current === key) {
      dropCheck()
      return
    }
    renewing.current = key
    const answer = await askCheck()
    if (renewing.current === key) renewing.current = ''
    if (!answer) return
    renewedFor.current = key
    // The group's last profile was onboarded meanwhile: the server has nothing left to judge.
    if ('check' in answer && !answer.check.issues.some(issue => issue.code === 'scope_unavailable')) showCheck(answer.check)
    else dropCheck()
  }, [askCheck, dropCheck, showCheck])

  const persist = useCallback((next: Preset) => {
    if (sent.current && samePreset(sent.current, next)) return
    sent.current = next
    saves.current = saves.current
      .then(() => autoOnboardingAPI.savePreset(next))
      .then(() => setSaveError(null))
      .catch(err => setSaveError(String(err instanceof Error ? err.message : err)))
  }, [])

  const describe = useCallback((change: Change): string => {
    if (change.kind === 'node') {
      return change.to
        ? t('the chosen node {from} is no longer among the usable nodes; changed to {to}', { from: change.from, to: change.to })
        : t('the chosen node {from} is no longer among the usable nodes', { from: change.from })
    }
    if (change.kind === 'proxy_group') return t('proxy group {from} belonged to the former node; changed to any group', { from: change.from })
    if (change.kind === 'proxy') return t('the chosen proxy is no longer on offer; pick a proxy again')
    if (change.kind === 'org') {
      return change.pickDomain
        ? t('the chosen organization is no longer among the organization profiles; changed to {to}, choose its email domain', { to: change.to })
        : t('the chosen organization is no longer among the organization profiles; changed to {to}', { to: change.to })
    }
    return t('the chosen email domain is no longer one of the organization’s; choose it again')
  }, [t])

  /** Take what the server and the platform say, and drop choices no longer on offer. */
  const apply = useCallback((data: Presets, scopeGroups: ScopeGroup[], how: 'load' | 'refresh' | 'replaced') => {
    let next = data.preset
    let changes: Change[] = []
    if (data.options) ({ preset: next, changes } = reconcile(next, data.options))
    next = { ...next, scope_group: scopeOf(next, scopeGroups) }
    sent.current = data.preset
    setConnection(data.connection)
    setOptions(data.options)
    setOptionsError(data.options_error)
    setGroups(scopeGroups)
    setPreset(next)
    dropCheck()
    persist(next)
    if (changes.length) {
      setNotice({ tone: 'warn', text: t('Changed since the last read: {changes}. Confirm the choices, then check again.', { changes: changes.map(describe).join('; ') }) })
    } else if (how === 'replaced') {
      setNotice({ tone: 'info', text: t('The token was replaced. The node and the organization were read anew for it; confirm them.') })
    } else if (how === 'refresh' && data.options) {
      setNotice({ tone: 'info', text: t('Read again what this token may use; the choices are all still on offer. The check has to be run again.') })
    } else {
      setNotice(null)
    }
  }, [describe, dropCheck, persist, t])

  const load = useCallback(async (how: 'load' | 'refresh' | 'replaced') => {
    const [data, scopes] = await Promise.all([autoOnboardingAPI.presets(), autoOnboardingAPI.scopes()])
    apply(data, scopes.groups, how)
  }, [apply])

  const start = useCallback(async () => {
    try {
      setError(null)
      await load('load')
    } catch (err) {
      setError(String(err instanceof Error ? err.message : err))
    } finally {
      setLoading(false)
    }
  }, [load])

  useEffect(() => {
    // Clears the previous error synchronously before awaiting; see the note
    // on the profiles page.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    start()
  }, [start])

  const scope = preset?.scope_group ?? null
  const scopeRef = useRef(scope)
  useEffect(() => { scopeRef.current = scope }, [scope])

  /** Read the chosen group's rows again, and what is left to onboard in every group. */
  const refreshRows = useCallback(async () => {
    const group = scopeRef.current
    if (!group) return
    try {
      const result = await autoOnboardingAPI.scopes(group)
      if (scopeRef.current !== group) return
      const fresh = result.rows ?? []
      setRows({ group, rows: fresh })
      void keepCheckTrue(fresh)
      // A group whose last row was just onboarded is no longer offered; it stays
      // on screen, with its counts, until another group is chosen.
      setGroups(current => {
        if (result.groups.some(item => item.id === group)) return result.groups
        const shownNow = current.find(item => item.id === group)
        if (!shownNow) return result.groups
        const onboarded = fresh.filter(row => row.onboarded).length
        return [{ ...shownNow, pending: fresh.length - onboarded, onboarded }, ...result.groups]
      })
    } catch {
      setRows(current => current ?? { group, rows: [] })
    }
  }, [keepCheckTrue])

  useEffect(() => {
    if (!scope) return
    // Rows of the group chosen before are not shown under this one.
    refreshRows()
  }, [scope, refreshRows])

  const loadRunning = useCallback(async () => {
    try {
      const response = await browsersAPI.active()
      setRunning(new Set(response.active_browsers.map(browser => browser.profile_id)))
    } catch {
      // The poll is best-effort; a transient failure must not surface as an error.
    }
  }, [])

  useEffect(() => {
    // The same poll as the profiles page: a browser run or stopped there shows here.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    loadRunning()
    const timer = setInterval(loadRunning, 5000)
    return () => clearInterval(timer)
  }, [loadRunning])

  useEffect(() => {
    if (!canManageAccess) return
    // What keeps a synchronized profile from being launched, as the profiles page reads it.
    profileSyncAPI.list().then(result => { if (Array.isArray(result.profiles)) setSyncBindings(result.profiles) }).catch(() => undefined)
  }, [canManageAccess])

  // An authorization left open is the platform's to finish, whether or not its
  // dialog is: the rows are read again now and then while one is.
  const anyOpen = (rows?.rows ?? []).some(row => isOpen(row.authorization))
  useEffect(() => {
    if (!anyOpen || dialog) return
    const timer = setInterval(refreshRows, 15000)
    return () => clearInterval(timer)
  }, [anyOpen, dialog, refreshRows])

  function change(patch: Partial<Preset>) {
    if (!preset) return
    const merged = { ...preset, ...patch }
    // Choosing an organization with a single domain chooses that domain too.
    const next = options ? reconcile(merged, options).preset : merged
    setPreset(next)
    dropCheck()
    setNotice(null)
    persist(next)
    if (patch.node_id && patch.node_id !== preset.node_id) loadPool(patch.node_id)
  }

  /** A proxy pool belongs to a node: read the one of the node just chosen. */
  async function loadPool(nodeId: string) {
    const request = ++poolRequest.current
    try {
      const data = await autoOnboardingAPI.presets(nodeId)
      if (request !== poolRequest.current) return
      setConnection(data.connection)
      setOptions(data.options)
      setOptionsError(data.options_error)
    } catch (err) {
      if (request === poolRequest.current) setSaveError(String(err instanceof Error ? err.message : err))
    }
  }

  async function saveToken(token: string): Promise<string | null> {
    setBusy('read')
    try {
      const result = await autoOnboardingAPI.saveToken(token)
      await load(result.replaced ? 'replaced' : 'load')
      return null
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === 'onboard_token_refused') {
        return connection?.saved
          ? t('The platform refused this token: it is invalid or was revoked. The saved token is unchanged.')
          : t('The platform refused this token: it is invalid or was revoked.')
      }
      if (err instanceof ApiRequestError && err.code === 'authorizations_open') {
        return t('Authorizations are still open ({rows}). An authorization belongs to the token that made it: submit the code or cancel first, then change the token.', {
          rows: err.message.includes(': ') ? err.message.slice(err.message.indexOf(': ') + 2) : '',
        })
      }
      return String(err instanceof Error ? err.message : err)
    } finally {
      setBusy(null)
    }
  }

  function markRow(profileId: string, state: RowBusy | null) {
    setRowBusy(current => {
      const next = new Map(current)
      if (state) next.set(profileId, state)
      else next.delete(profileId)
      return next
    })
  }

  function rowError(profileId: string, text: string | null) {
    setRowErrors(current => {
      const next = new Map(current)
      if (text) next.set(profileId, text)
      else next.delete(profileId)
      return next
    })
  }

  /** Put a refusal into words; one that says the token is refused is also shown on the card. */
  function said(err: unknown): string {
    if (err instanceof ApiRequestError) {
      if (err.code === 'onboard_token_refused') load('load').catch(() => undefined)
      const refusal = refusalSaid(err.code, err.message)
      return t(refusal.text, refusal.values)
    }
    return String(err instanceof Error ? err.message : err)
  }

  // Run and Stop are the profiles page's own: the same requests, the same refusals.
  async function run(row: ScopeRow) {
    copyEmailOnClick(row.email, t, toast)
    markRow(row.profile_id, 'run')
    if (await launchBrowser(row.profile_id, t, toast)) await loadRunning()
    markRow(row.profile_id, null)
  }

  async function stop(row: ScopeRow): Promise<boolean> {
    markRow(row.profile_id, 'stop')
    const stopped = await stopBrowser(row.profile_id, t, toast)
    if (stopped) await loadRunning()
    markRow(row.profile_id, null)
    return stopped
  }

  /** Have a link made for a row. The server opens it in the row's browser. */
  async function makeLink(row: ScopeRow) {
    rowError(row.profile_id, null)
    markRow(row.profile_id, 'link')
    try {
      await saves.current
      const job = await autoOnboardingAPI.createJob(row.profile_id)
      setDialog({ profileId: row.profile_id, job })
    } catch (err) {
      rowError(row.profile_id, said(err))
    } finally {
      markRow(row.profile_id, null)
      refreshRows()
    }
  }

  /** Come back to a row's open authorization: the same link, opened nowhere again. */
  async function continueAuthorization(row: ScopeRow) {
    rowError(row.profile_id, null)
    try {
      setDialog({ profileId: row.profile_id, job: await autoOnboardingAPI.job(row.profile_id) })
    } catch (err) {
      rowError(row.profile_id, said(err))
    }
  }

  function takeJob(profileId: string, job: RowJob) {
    // Whether the link was opened is said once, when it is made.
    setDialog(current => (current && current.profileId === profileId ? { profileId, job: { ...job, opened: current.job.opened, opened_in: current.job.opened_in } } : current))
    // The row behind the dialog says the same as the dialog does.
    if (dialog?.job.authorization?.status !== job.authorization?.status) refreshRows()
  }

  async function pollJob(profileId: string) {
    try {
      takeJob(profileId, await autoOnboardingAPI.job(profileId))
    } catch (err) {
      if (err instanceof ApiRequestError && err.code === 'onboard_token_refused') {
        setDialog(null)
        rowError(profileId, said(err))
        refreshRows()
      }
    }
  }

  async function submitCode(profileId: string, code: string): Promise<string | null> {
    try {
      takeJob(profileId, await autoOnboardingAPI.submitCode(profileId, code))
      return null
    } catch (err) {
      return said(err)
    }
  }

  async function cancelAuthorization(profileId: string): Promise<string | null> {
    try {
      const job = await autoOnboardingAPI.cancelJob(profileId)
      // One that finished meanwhile stays finished, and is shown; a cancelled one closes.
      if (job.onboarded) takeJob(profileId, job)
      else setDialog(null)
      refreshRows()
      return null
    } catch (err) {
      return said(err)
    }
  }

  function closeDialog() {
    setDialog(null)
    refreshRows()
  }

  async function refresh() {
    setBusy('refresh')
    try {
      await load('refresh')
    } catch (err) {
      setNotice({ tone: 'warn', text: String(err instanceof Error ? err.message : err) })
    } finally {
      setBusy(null)
    }
  }

  async function runCheck() {
    setCheckError(null)
    const answer = await askCheck()
    if (!answer) return
    if ('check' in answer) {
      showCheck(answer.check)
      return
    }
    showCheck(null)
    const err = answer.error
    if (err instanceof ApiRequestError && err.code === 'onboard_token_refused') {
      // The saved token was revoked in the meantime: show it as refused.
      await load('load').catch(() => undefined)
      setCheckError(t('The platform refused the saved token: it is invalid or was revoked. Change it to a valid one first.'))
    } else if (err instanceof ApiRequestError && err.code === 'oneday_unavailable') {
      setCheckError(t('The platform could not be reached, so nothing was checked. Try again.'))
    } else {
      setCheckError(String(err instanceof Error ? err.message : err))
    }
  }

  if (error) {
    return (
      <>
        <Header title={t('Auto onboarding')} />
        <EmptyState
          icon={<UserRoundCheck size={18} />}
          title={t('Cannot reach the API')}
          body={error}
          action={<button className="btn btn-default" onClick={start}>{t('Retry')}</button>}
        />
      </>
    )
  }
  if (loading || !connection || !preset) {
    return (
      <>
        <Header title={t('Auto onboarding')} />
        <p className="px-5 py-8 text-ink-faint">{t('Loading…')}</p>
      </>
    )
  }

  const usable = connection.saved && !connection.refused && options !== null
  const locked = busy !== null || tokenEditing
  const shown = scope && rows?.group === scope ? rows.rows : []
  const formatTime = (iso: string) => new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
  const dialogRow = dialog ? shown.find(row => row.profile_id === dialog.profileId) : undefined
  const checked = new Map((check?.rows ?? []).map(row => [row.profile_id, row]))
  const noteOf = (row: ScopeRow): string => {
    const note = exitNote(row, preset, usable ? options : null)
    if (note.kind === 'matched') return t('matches a proxy in the node’s pool')
    if (note.kind === 'unknown') return t('proxy never checked')
    if (note.kind === 'failed') return t('last proxy check failed')
    if (note.kind === 'not_in_pool') return t('not in the node’s pool, or full')
    if (note.kind === 'ambiguous') return t('several pool proxies come out here')
    if (note.kind === 'platform') return t('proxy assigned by the platform')
    if (note.kind === 'pool') return note.exitIp ? t('onboarded through {exit}', { exit: note.exitIp }) : t('no proxy picked yet')
    return connection.refused ? t('token invalid, cannot match') : t('matched once the token is read')
  }

  return (
    <>
      <Header title={t('Auto onboarding')} />
      <div className="flex max-w-[980px] flex-col gap-4 p-5">
        <p className="text-ink-dim">{t('Keeps the onboarding parameters, runs a row’s browser, and has its account authorized by hand: you sign in and consent yourself.')}</p>
        <p className="text-ink-dim">{t('Profiles opened here keep the independent fingerprint assigned when they were created. This page does not clone or replace their devices.')}</p>

        <section className="panel" aria-label={t('Onboarding parameters')}>
          <h2 className="border-b border-line px-4 py-3 text-[13px] font-semibold">{t('Onboarding parameters')}</h2>

          <div className="m-4 overflow-hidden rounded-lg border border-line bg-canvas" data-testid="preset-card">
            <h3 className="border-b border-line px-4 py-3 font-semibold">{t('Platform access preset')}</h3>
            <TokenRow
              connection={connection}
              counts={usable && options ? { nodes: options.nodes.length, orgs: options.orgs.length } : null}
              busy={busy === 'check' ? null : busy}
              notice={notice}
              onSave={saveToken}
              onRefresh={refresh}
              onEditing={setTokenEditing}
            />
            {usable && options ? (
              <PresetSteps preset={preset} options={options} disabled={locked} onChange={change} />
            ) : (
              <p className="px-4 py-4 text-[12px] text-ink-dim" data-testid="preset-empty">
                {!connection.saved
                  ? t('Once an onboarder token is saved and read, the nodes and organizations it may use are shown here.')
                  : connection.refused
                    ? t('The saved token can no longer be used. Once it is changed to a valid one, the nodes and organizations it may use are shown here again.')
                    : t(OPTIONS_ERRORS[optionsError ?? ''] ?? OPTIONS_ERRORS.default)}
              </p>
            )}
          </div>

          <div className="px-4 pb-4">
            <label className="field-label" htmlFor="onboard-scope">{t('Profiles to onboard')}</label>
            <select id="onboard-scope" className="field max-w-[460px]" value={scope ?? ''} disabled={locked || groups.length === 0} onChange={event => change({ scope_group: event.target.value })}>
              {groups.length === 0 && <option value="">{t('No profiles to onboard')}</option>}
              {groups.map(group => (
                <option key={group.id} value={group.id}>{t('{name} · to onboard {pending} · onboarded {onboarded}', { name: group.name, pending: group.pending, onboarded: group.onboarded })}</option>
              ))}
            </select>
            <p className="mt-1 text-[12px] text-ink-dim">{t('Chosen by group. Only profiles that have an email address and are not onboarded yet are counted; a profile outside every group cannot be onboarded.')}</p>
          </div>
        </section>

        {saveError && <p className="text-danger" role="alert">{t('The last choice could not be saved: {error}', { error: saveError })}</p>}

        <section className="flex flex-wrap items-start justify-between gap-3" aria-label={t('Check')}>
          <div className="min-w-0 flex-1" role="status" data-testid="check-result">
            {checkError ? (
              <p className="text-danger">{checkError}</p>
            ) : check ? (
              <>
                <p className={check.ok ? 'font-medium text-ok' : 'font-medium text-warn'}>
                  {check.ok
                    ? t('Check passed: {ready} of {total} profiles would be onboarded.', { ready: check.ready, total: check.rows.length })
                    : t('The parameters would not do yet.')}
                </p>
                {check.issues.length > 0 && (
                  <ul className="mt-1 list-disc pl-5 text-ink-dim">
                    {check.issues.map(issue => <li key={issue.code}>{t(ISSUE_MESSAGES[issue.code] ?? issue.code)}</li>)}
                  </ul>
                )}
              </>
            ) : (
              <p className="text-ink-dim">{tokenEditing ? t('Finish or cancel the token change before checking.') : t('Not checked yet.')}</p>
            )}
          </div>
          <button type="button" className="btn btn-primary" disabled={!usable || locked} onClick={runCheck}>
            {busy === 'check' ? t('Checking…') : t('Check parameters')}
          </button>
        </section>

        <table className="w-full border-collapse" aria-label={t('Profiles in the chosen group')}>
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-[0.05em] text-ink-faint">
              <th className="w-[24%] py-2 pr-4 font-medium">{t('Profile / email')}</th>
              <th className="w-[19%] py-2 pr-4 font-medium">{t('Own exit')}</th>
              <th className="py-2 pr-4 font-medium">{t('Status')}</th>
              <th className="w-[230px] py-2 font-medium">{t('Actions')}</th>
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 && (
              <tr>
                <td colSpan={4} className="py-6 text-center text-ink-dim">
                  {groups.length === 0
                    ? t('No profiles to onboard. Only profiles in a group that have an email address and are not onboarded yet are listed here.')
                    : t('Loading…')}
                </td>
              </tr>
            )}
            {shown.map(row => {
              const result = checked.get(row.profile_id)
              const state = rowBusy.get(row.profile_id)
              const isRunning = running.has(row.profile_id)
              const open = isOpen(row.authorization)
              const authorization = authorizationSaid(row, connection.refused, formatTime)
              const blocked = linkBlock(row, connection, { running: isRunning || state === 'run', editingToken: tokenEditing })
              const syncBlocked = syncBlocksLaunch(syncBindings.find(binding => binding.local_id === row.profile_id))
              const problem = rowErrors.get(row.profile_id)
              return (
                <tr key={row.profile_id} className="border-b border-line/60 align-top" data-profile={row.profile_id}>
                  <td className="py-2.5 pr-4">
                    {row.name}
                    <div className="font-mono text-[12px] text-ink-dim">{row.email}</div>
                  </td>
                  <td className="py-2.5 pr-4">
                    <span className="font-mono">{row.exit_ip ?? '—'}</span>
                    <div className="text-[12px] text-ink-dim">{noteOf(row)}</div>
                  </td>
                  <td className="py-2.5 pr-4" data-testid="row-state">
                    <div data-testid="browser-state">
                      <span className="text-ink-dim">{t('Browser')}</span>{' '}
                      {state === 'run' ? <span className="text-signal">{t('Starting…')}</span>
                        : state === 'stop' ? <span className="text-signal">{t('Closing…')}</span>
                        : isRunning ? <span className="text-ok">{t('Running')}</span>
                        : t('Stopped')}
                    </div>
                    <div className="mt-0.5" data-testid="authorization-state">
                      <span className="text-ink-dim">{t('Authorization')}</span>{' '}
                      {state === 'link' ? <span className="text-signal">{t('Making the authorize link…')}</span> : (
                        <>
                          <span className={TONES[authorization.tone]}>{t(authorization.text, authorization.values)}</span>
                          {row.authorization?.status === 'pending' && !connection.refused && (
                            <> · {t('time left')} <TimeLeft until={row.authorization.expires_at} onLapsed={refreshRows} /></>
                          )}
                        </>
                      )}
                    </div>
                    {state !== 'link' && authorization.note && <div className="text-[12px] text-ink-dim">{t(authorization.note, authorization.noteValues)}</div>}
                    {state !== 'link' && authorization.made && (
                      <div className="text-[12px] text-ink-dim">
                        {t('This authorization: {node} · {proxy}', { node: authorization.made.node, proxy: t(authorization.made.proxy.text, authorization.made.proxy.values) })}
                      </div>
                    )}
                    {/* A row onboarded since the check is not said to be still to onboard while the check is renewed. */}
                    {result && !(row.onboarded && result.status !== 'onboarded') && (
                      <div className={`text-[12px] ${result.status === 'ready' ? 'text-ok' : result.status === 'onboarded' ? 'text-ink-dim' : 'text-warn'}`} data-testid="row-check">
                        {t('Check: {result}', { result: t(ROW_MESSAGES[result.status] ?? result.status) })}
                      </div>
                    )}
                    {problem && <div className="mt-0.5 text-[12px] text-danger" role="alert" data-testid="row-error">{problem}</div>}
                  </td>
                  <td className="py-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <button
                        type="button"
                        className="btn btn-default h-7"
                        data-action="browser"
                        disabled={state !== undefined || !row.can_launch || (!isRunning && syncBlocked)}
                        onClick={() => (isRunning ? stop(row) : run(row))}
                      >
                        {isRunning ? (
                          <>
                            <Square size={11} fill="currentColor" />
                            {t('Stop')}
                          </>
                        ) : (
                          <>
                            <Play size={11} fill="currentColor" />
                            {t('Run')}
                          </>
                        )}
                      </button>
                      {open ? (
                        <button type="button" className="btn btn-primary h-7" data-action="authorize" disabled={connection.refused || state !== undefined} onClick={() => continueAuthorization(row)}>
                          {t('Continue authorization')}
                        </button>
                      ) : (
                        <button type="button" className="btn btn-default h-7" data-action="authorize" disabled={blocked !== null || state !== undefined} onClick={() => makeLink(row)}>
                          {t('Make authorize link')}
                        </button>
                      )}
                    </div>
                    {(open ? (connection.refused ? 'Token invalid' : null) : blocked) && (
                      <div className="mt-1 text-[12px] text-ink-dim" data-testid="link-block">{t((open ? 'Token invalid' : blocked) ?? '')}</div>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        <p className="text-[12px] text-ink-dim" data-testid="run-note">
          {t('Run and Stop work as on the profiles page: Run opens the start page, and with automation switched on it also enters the row’s address and presses SSO once. Signing in and consenting are done by hand; nothing else is pressed for you.')}
        </p>
      </div>
      {dialog && dialogRow && (
        <AuthorizeDialog
          key={dialog.profileId}
          row={dialogRow}
          job={dialog.job}
          running={running.has(dialog.profileId)}
          browserBusy={rowBusy.get(dialog.profileId) === 'stop'}
          canRegenerate={linkBlock(dialogRow, connection, { running: running.has(dialog.profileId), editingToken: tokenEditing }) === null}
          onClose={closeDialog}
          onPoll={() => pollJob(dialog.profileId)}
          onSubmit={code => submitCode(dialog.profileId, code)}
          onCancel={() => cancelAuthorization(dialog.profileId)}
          onCloseBrowser={async () => {
            // Once the account is onboarded there is nothing left to do here.
            if ((await stop(dialogRow)) && dialog.job.onboarded) closeDialog()
          }}
          onRegenerate={() => {
            setDialog(null)
            makeLink(dialogRow)
          }}
        />
      )}
    </>
  )
}

function Header({ title }: { title: string }) {
  return (
    <header className="sticky top-0 z-20 flex h-[52px] items-center gap-3 border-b border-line bg-canvas/85 px-5 backdrop-blur">
      <h1 className="text-[14px] font-semibold">{title}</h1>
    </header>
  )
}
