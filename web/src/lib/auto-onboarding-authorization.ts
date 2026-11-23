/**
 * How a row's authorization is put into words, kept apart from the screen so it can be tested.
 *
 * Every text here is an English source string for the catalogs; the screen
 * passes it through t() with the values given beside it.
 */
import type { Authorization, Connection, ScopeRow } from './auto-onboarding-api'
import { ISSUE_MESSAGES, ROW_MESSAGES } from './auto-onboarding-preset'

export type Tone = 'ok' | 'warn' | 'danger' | 'busy' | 'dim'

export interface Said {
  tone: Tone
  text: string
  values?: Record<string, string | number>
  /** A second, quieter line. */
  note?: string
  noteValues?: Record<string, string | number>
  /** In place of a note: the node and the proxy an open authorization was made with. */
  made?: { node: string; proxy: Pick<Said, 'text' | 'values'> }
}

/** What the platform's failure codes mean for the person at the screen. */
const FAILURES: Record<string, { text: string; note: string; tone?: Tone }> = {
  account_exists: { tone: 'warn', text: 'The platform already has this account, to be looked into', note: 'No account was added and the one there was not changed. This row is not counted as onboarded.' },
  account_active: { tone: 'warn', text: 'The platform already has this account, to be looked into', note: 'No account was added and the one there was not changed. This row is not counted as onboarded.' },
  domain_mismatch: { text: 'The account’s address is not under the chosen domain', note: 'The account signed in to in the browser is outside the chosen domain. Nothing was added.' },
  team_mismatch: { text: 'The account does not belong to the chosen organization', note: 'Sign in to an account of the chosen organization, or choose the organization it belongs to.' },
  organization_mismatch: { text: 'The account does not belong to the chosen organization', note: 'Sign in to an account of the chosen organization, or choose the organization it belongs to.' },
  proxy_team_mismatch: { text: 'This exit already carries another organization', note: 'Choose another proxy, or the organization this exit belongs to.' },
  team_identity_invalid: { text: 'The account does not belong to the chosen organization', note: 'Sign in to an account of the chosen organization, or choose the organization it belongs to.' },
  token_exchange_failed: { text: 'The authorization code was used already or is no longer valid', note: 'Make a new authorize link and consent once more.' },
  invalid_grant: { text: 'The authorization code was used already or is no longer valid', note: 'Make a new authorize link and consent once more.' },
  callback_expired: { tone: 'warn', text: 'The authorization lapsed', note: 'No code was handed over within 30 minutes, and the platform freed what it had reserved. Make a new authorize link.' },
  job_expired: { tone: 'warn', text: 'The authorization lapsed', note: 'The platform did not finish in time and freed what it had reserved. Make a new authorize link.' },
  job_cancelled: { tone: 'dim', text: 'Cancelled', note: 'The platform cancelled this authorization and freed what it had reserved. A new link can be made.' },
  authorize_failed: { text: 'The authorization was not completed', note: 'An onboarder token is not told more than that. Make a new authorize link and try again.' },
  validate_failed: { text: 'The authorization was not completed', note: 'An onboarder token is not told more than that. Make a new authorize link and try again.' },
}

/** Where an authorization's proxy comes out, or that the platform picks it. */
export function proxySaid(proxy: string | null): Pick<Said, 'text' | 'values'> {
  if (!proxy) return { text: 'proxy unknown' }
  if (proxy === 'auto') return { text: 'assigned by the platform' }
  if (proxy.startsWith('auto:')) return { text: 'assigned within group {group}', values: { group: proxy.slice(5) } }
  return { text: '{exit}', values: { exit: proxy } }
}

/** How a finished authorization came out, for the row and for the dialog. */
export function outcomeSaid(authorization: Authorization, email: string, formatTime: (iso: string) => string = iso => iso): Said {
  if (authorization.status === 'lost') {
    return { tone: 'warn', text: 'This authorization can no longer be asked about', note: 'It was made with a token that is no longer in use; the platform lets it lapse by itself. A new link can be made.' }
  }
  if (authorization.status === 'done') {
    if (authorization.outcome === 'mismatch') {
      return {
        tone: 'danger',
        text: 'The account onboarded is not this row’s',
        note: '{account} was onboarded, not this row’s {email}. The account is on the platform now and cannot be taken back from here; ask an administrator. This row is not counted as onboarded.',
        noteValues: { account: authorization.account_email ?? '—', email },
      }
    }
    if (authorization.outcome === 'reused') {
      return { tone: 'ok', text: 'The platform already had this account and authorized it again', note: 'No account was added. This row is counted as onboarded.' }
    }
    return authorization.cooldown_until
      ? { tone: 'ok', text: 'Onboarded', note: '{email} joined the platform’s default group. A new account rests until {time}; the platform does not schedule it before then.', noteValues: { email: authorization.account_email ?? email, time: formatTime(authorization.cooldown_until) } }
      : { tone: 'ok', text: 'Onboarded', note: '{email} joined the platform’s default group.', noteValues: { email: authorization.account_email ?? email } }
  }
  const failure = FAILURES[authorization.error_code ?? '']
  if (failure) return { tone: failure.tone ?? 'danger', text: failure.text, note: failure.note }
  return { tone: 'danger', text: 'The authorization failed ({code})', values: { code: authorization.error_code ?? 'authorization_failed' }, note: 'Make a new authorize link and try again.' }
}

/** A row's authorization in its state column. `refused` is whether the saved token is. */
export function authorizationSaid(row: ScopeRow, refused: boolean, formatTime: (iso: string) => string = iso => iso): Said {
  const authorization = row.authorization
  if (!authorization) {
    return row.onboarded
      ? { tone: 'dim', text: 'Already onboarded', note: 'Onboarded earlier; it cannot be onboarded again.' }
      : { tone: 'dim', text: 'No link made yet' }
  }
  const with_ = { made: { node: authorization.node ?? '—', proxy: proxySaid(authorization.proxy) } }
  if (authorization.status === 'pending' || authorization.status === 'working') {
    if (refused) return { tone: 'danger', text: 'Cannot be asked about · token invalid', ...with_ }
    return authorization.status === 'pending'
      ? { tone: 'warn', text: 'Waiting for the authorization code', ...with_ }
      : { tone: 'busy', text: 'The platform is working on it', ...with_ }
  }
  return outcomeSaid(authorization, row.email, formatTime)
}

/** Whether an authorization is still the platform's to finish. */
export function isOpen(authorization: Authorization | null): boolean {
  return authorization?.status === 'pending' || authorization?.status === 'working'
}

/**
 * Why a row cannot be given an authorize link right now, said beside its button.
 * Null when it can be asked for; the server judges the rest.
 */
export function linkBlock(row: ScopeRow, connection: Connection, state: { running: boolean; editingToken: boolean }): string | null {
  if (row.onboarded) return 'Already onboarded'
  if (!connection.saved) return 'No token saved yet'
  if (connection.refused) return 'Token invalid'
  if (state.editingToken) return 'The token is being changed'
  if (!row.can_launch) return 'No permission to run this profile'
  if (!state.running) return 'Run the browser first'
  return null
}

/** Minutes and seconds an authorization has left, by the platform's own expiry. */
export function timeLeft(expiresAt: string | null, now: number): string | null {
  if (!expiresAt) return null
  const until = Date.parse(expiresAt)
  if (Number.isNaN(until)) return null
  const seconds = Math.max(0, Math.floor((until - now) / 1000))
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
}

/** What the platform said when it would not make a link, by its own code. */
const PLATFORM_REFUSALS: Record<string, string> = {
  platform_proxy_full: 'The platform refused: this exit has no room for another account. Choose another way to pick the proxy, or wait for room.',
  platform_proxy_unassignable: 'The platform refused: this exit cannot take this account, for instance because another organization holds it.',
  platform_proxy_not_found: 'The platform refused: the proxy is no longer in the node’s pool. Refresh and check the parameters.',
  platform_proxy_pool_empty: 'The platform refused: it found no proxy that can take this account.',
  platform_needs_virgin_exit: 'The platform refused: with the organization unspecified it needs an exit that carries no account yet.',
  platform_node_at_capacity: 'The platform refused: the node is full. No row on this node can be given a link now.',
  platform_node_offline: 'The platform refused: the node is offline.',
  platform_group_invalid: 'The platform refused: the node has no such proxy group. Refresh and check the parameters.',
  platform_org_required: 'The platform refused: an organization must be chosen.',
  platform_org_not_allowed: 'The platform refused: this token may not onboard into that organization.',
  platform_domain_invalid: 'The platform refused: the email domain is not one of the organization’s.',
  platform_invalid_request: 'The platform refused the request: {message}',
}

const LOCAL_REFUSALS: Record<string, string> = {
  onboard_token_missing: 'Save an onboarder token first.',
  onboard_token_refused: 'The platform refused the saved token: it is invalid or was revoked. Change it to a valid one first.',
  browser_not_running: 'Run this row’s browser first: the link is opened in it.',
  onboarded: 'This row is already onboarded.',
  row_unavailable: 'Only a profile in a group that has an email address can be onboarded.',
  authorization_not_waiting: 'This row has no authorization waiting any more.',
  callback_invalid: 'The platform refused this code (callback_invalid): it is incomplete, or it belongs to another row. This authorization goes on waiting; paste again.',
  oneday_unavailable: 'The platform could not be reached. Try again.',
}

/** Why an authorize link, a code or a cancel was refused, in words for the screen. */
export function refusalSaid(code: string | undefined, message: string): Pick<Said, 'text' | 'values'> {
  const key = code ?? ''
  if (LOCAL_REFUSALS[key]) return { text: LOCAL_REFUSALS[key] }
  if (ISSUE_MESSAGES[key]) return { text: ISSUE_MESSAGES[key] }
  if (ROW_MESSAGES[key] && key !== 'ready') return { text: ROW_MESSAGES[key] }
  if (PLATFORM_REFUSALS[key]) return { text: PLATFORM_REFUSALS[key], values: key === 'platform_invalid_request' ? { message } : undefined }
  if (key.startsWith('platform_')) return { text: 'The platform refused ({code}): {message}', values: { code: key.slice(9), message } }
  return { text: '{message}', values: { message } }
}

/** Every source string this module can hand to t(), for the catalogs to be checked against. */
export const AUTHORIZATION_TEXTS: string[] = [
  ...Object.values(FAILURES).flatMap(failure => [failure.text, failure.note]),
  ...Object.values(PLATFORM_REFUSALS),
  ...Object.values(LOCAL_REFUSALS),
  'proxy unknown',
  'assigned by the platform',
  'assigned within group {group}',
  'This authorization can no longer be asked about',
  'It was made with a token that is no longer in use; the platform lets it lapse by itself. A new link can be made.',
  'The account onboarded is not this row’s',
  '{account} was onboarded, not this row’s {email}. The account is on the platform now and cannot be taken back from here; ask an administrator. This row is not counted as onboarded.',
  'The platform already had this account and authorized it again',
  'No account was added. This row is counted as onboarded.',
  'Onboarded',
  '{email} joined the platform’s default group. A new account rests until {time}; the platform does not schedule it before then.',
  '{email} joined the platform’s default group.',
  'The authorization failed ({code})',
  'Make a new authorize link and try again.',
  'Already onboarded',
  'Onboarded earlier; it cannot be onboarded again.',
  'No link made yet',
  'Cannot be asked about · token invalid',
  'Waiting for the authorization code',
  'The platform is working on it',
  'No token saved yet',
  'Token invalid',
  'The token is being changed',
  'No permission to run this profile',
  'Run the browser first',
  'The platform refused ({code}): {message}',
]
