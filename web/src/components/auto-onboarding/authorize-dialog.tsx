'use client'

import { useEffect, useState } from 'react'

import { useI18n } from '@/components/i18n-provider'
import { Modal } from '@/components/modal'
import type { RowJob, ScopeRow } from '@/lib/auto-onboarding-api'
import { isOpen, outcomeSaid, timeLeft, type Tone } from '@/lib/auto-onboarding-authorization'
import { copyText } from '@/lib/clipboard'

export const TONES: Record<Tone, string> = {
  ok: 'text-ok',
  warn: 'text-warn',
  danger: 'text-danger',
  busy: 'text-signal',
  dim: 'text-ink-dim',
}

/** The time an authorization has left, by the platform's own expiry, counted down on screen. */
export function TimeLeft({ until, onLapsed }: { until: string | null; onLapsed?: () => void }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const left = timeLeft(until, now)
  const lapsed = left === '00:00'
  useEffect(() => {
    if (lapsed) onLapsed?.()
    // Only the moment it lapses matters, not a new callback on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lapsed])
  return <span className="tabular-nums" data-testid="time-left">{left ?? '—'}</span>
}

/**
 * Finishing one row's authorization by hand.
 *
 * The link was opened in the row's browser when it was made; the person
 * consents there and pastes back the code the page then shows. Closing this
 * dialog cancels nothing: the authorization goes on waiting and the row
 * offers to continue. Nothing here signs in or consents.
 */
export function AuthorizeDialog({
  row,
  job,
  running,
  browserBusy,
  canRegenerate,
  onClose,
  onPoll,
  onSubmit,
  onCancel,
  onCloseBrowser,
  onRegenerate,
}: {
  row: ScopeRow
  job: RowJob
  /** Whether the row's browser is running. */
  running: boolean
  browserBusy: boolean
  canRegenerate: boolean
  onClose: () => void
  /** Ask the platform how the authorization stands. */
  onPoll: () => void
  /** Resolves to why the code was refused, or null once the platform has it. */
  onSubmit: (code: string) => Promise<string | null>
  /** Resolves to why it could not be cancelled, or null. */
  onCancel: () => Promise<string | null>
  onCloseBrowser: () => void
  onRegenerate: () => void
}) {
  const { t, locale } = useI18n()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState<'yes' | 'no' | null>(null)
  const [acting, setActing] = useState<'submit' | 'cancel' | null>(null)
  const authorization = job.authorization
  const status = authorization?.status ?? 'lost'

  // The platform is asked while there is something to wait for: often while it
  // works on a code, now and then while the link waits for one.
  useEffect(() => {
    if (status !== 'working' && status !== 'pending') return
    const timer = setInterval(onPoll, status === 'working' ? 2000 : 10000)
    return () => clearInterval(timer)
    // onPoll is a new function on every render of the page; the interval is per status.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status])

  if (!authorization) return null

  async function submit() {
    if (!code.trim() || acting) return
    setActing('submit')
    setError(null)
    const refusal = await onSubmit(code.trim())
    setActing(null)
    if (refusal) setError(refusal)
    else setCode('')
  }

  async function cancel() {
    if (acting) return
    setActing('cancel')
    setError(null)
    const refusal = await onCancel()
    setActing(null)
    if (refusal) setError(refusal)
  }

  async function copy() {
    if (!job.authorize_url) return
    setCopied((await copyText(job.authorize_url)) ? 'yes' : 'no')
  }

  const formatTime = (iso: string) => new Date(iso).toLocaleTimeString(locale, { hour: '2-digit', minute: '2-digit' })
  const result = status === 'pending' || status === 'working' ? null : outcomeSaid(authorization, row.email, formatTime)
  const where = browserBusy
    ? t('This row’s browser is closing…')
    : !running
      ? t('This row’s browser is stopped, and the authorize page went with it. Close this dialog, press Run on the row, come back with “Continue authorization” and copy the link into the row’s browser; or cancel this authorization.')
      : job.opened === true && job.opened_in === 'window'
        ? t('The link was opened in a new window of this row’s browser, because no tab could be opened in the window that was there. Check there that {email} is signed in, then consent; nothing is pressed for you.', { email: row.email })
      : job.opened === true
        ? t('The link was opened in a new tab of this row’s browser. Check there that {email} is signed in, then consent; nothing is pressed for you.', { email: row.email })
        : job.opened === false
          ? t('The link could not be opened in this row’s browser. Press Copy and paste it into the address bar of this row’s browser.')
          : t('The link was opened in this row’s browser when it was made. If that tab is gone, press Copy and paste the link into the address bar of this row’s browser.')

  return (
    <Modal
      open
      title={t('Complete the authorization')}
      subtitle={`${row.name} · ${row.email}`}
      onClose={onClose}
      width={660}
      footer={
        <>
          <button type="button" className="btn btn-ghost mr-auto" disabled={!running || browserBusy} onClick={onCloseBrowser} data-testid="close-browser">
            {browserBusy ? t('Closing…') : t('Close browser')}
          </button>
          {isOpen(authorization) && (
            <button type="button" className="btn btn-default" disabled={acting !== null} onClick={cancel} data-testid="cancel-authorization">
              {acting === 'cancel' ? t('Cancelling…') : t('Cancel')}
            </button>
          )}
          {status === 'pending' && (
            <button type="button" className="btn btn-primary" disabled={!code.trim() || acting !== null} onClick={submit} data-testid="submit-code">
              {acting === 'submit' ? t('Submitting…') : t('Submit the authorization code')}
            </button>
          )}
          {result && !job.onboarded && canRegenerate && (
            <button type="button" className="btn btn-default" onClick={onRegenerate} data-testid="regenerate">{t('Make a new link')}</button>
          )}
          {result && (
            <button type="button" className={job.onboarded ? 'btn btn-primary' : 'btn btn-default'} onClick={onClose} data-testid="dialog-done">
              {job.onboarded ? t('Done') : t('Close')}
            </button>
          )}
        </>
      }
    >
      {status === 'pending' && (
        <div data-testid="authorize-pending">
          <p className="rounded-md border border-warn/40 px-3 py-2 text-warn">
            {t('Open this link in the network this account normally uses, such as the fingerprint browser set to the same proxy. Authorizing from another IP may get the account flagged.')}
          </p>
          <p className={`mt-2 text-[12px] ${running || browserBusy ? 'text-ink-dim' : 'text-danger'}`} role="status" data-testid="link-where">{where}</p>
          <div className="mt-3 flex items-stretch gap-3 rounded-md border border-line bg-canvas p-3">
            <code className="max-h-28 min-w-0 flex-1 select-all overflow-auto break-all font-mono text-[12px]" data-testid="authorize-url">
              {job.authorize_url ?? t('The link could not be read from the platform just now.')}
            </code>
            <button type="button" className="btn btn-default shrink-0 self-start" disabled={!job.authorize_url} onClick={copy} data-testid="copy-link">{t('Copy')}</button>
          </div>
          {copied && (
            <p className="mt-1 text-[12px] text-ink-dim" role="status">
              {copied === 'yes' ? t('Link copied. Open it only in this row’s browser.') : t('The link could not be copied. Select it and copy it by hand; open it only in this row’s browser.')}
            </p>
          )}
          <label className="field-label mt-4" htmlFor="authorize-code">{t('Authorization code')}</label>
          <textarea
            id="authorize-code"
            className="field font-mono"
            rows={2}
            autoComplete="off"
            spellCheck={false}
            maxLength={4000}
            placeholder={t('Paste the authorization code the authorize page shows, like xxxxxxxx…#xxxxxxxx…')}
            value={code}
            disabled={acting !== null}
            onChange={event => { setCode(event.target.value); setError(null) }}
            data-modal-autofocus
          />
          <p className="mt-1 text-[12px] text-ink-dim">
            {t('After you consent, the page shows an authorization code. Use the page’s copy button and paste the code here whole, including the # and what follows it.')}{' '}
            {t('This link is valid for another')} <TimeLeft until={authorization.expires_at} onLapsed={onPoll} />
          </p>
        </div>
      )}
      {status === 'working' && (
        <div data-testid="authorize-working">
          <p className="font-medium text-signal">{t('The platform is finishing the onboarding…')}</p>
          <p className="mt-1 text-[12px] text-ink-dim">{t('The code was handed over; the platform takes up to 3 minutes. You may close this dialog: the result is kept on the row.')}</p>
        </div>
      )}
      {result && (
        <div data-testid="authorize-result">
          <p className={`font-medium ${TONES[result.tone]}`} data-testid="result-title">
            {t(result.text, result.values)}{authorization.error_code && !result.values ? ` (${authorization.error_code})` : ''}
          </p>
          {result.note && <p className="mt-1 text-[12px] text-ink-dim">{t(result.note, result.noteValues)}</p>}
        </div>
      )}
      {error && <p className="mt-3 text-danger" role="alert" data-testid="dialog-error">{error}</p>}
      {isOpen(authorization) && (
        <p className="mt-4 text-[12px] text-ink-dim">
          {t('Closing this dialog does not cancel the authorization; come back with “Continue authorization” on the row. Cancel has the platform cancel it and free what it reserved. Close browser only closes this row’s browser.')}
        </p>
      )}
    </Modal>
  )
}
