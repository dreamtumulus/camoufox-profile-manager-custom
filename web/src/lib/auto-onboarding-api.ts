import { apiRequest } from './api'

const PREFIX = '/api/v1/auto-onboarding'

/**
 * How a batch's accounts get their proxy on the platform: every row through
 * the pool entry that has its own browser exit, a proxy the platform picks,
 * or one entry of the node's pool for every row.
 */
export type ProxyMode = 'row' | 'auto' | 'pool'

/** What is chosen on the access preset card. Saved as chosen, finished or not. */
export interface Preset {
  node_id: string | null
  proxy_mode: ProxyMode
  proxy_group: string | null
  proxy_id: string | null
  org_id: string | null
  domain: string | null
  /** Only for a token without organization profiles: name the organization by UUID. */
  org_manual: boolean
  org_uuid: string | null
  note: string | null
  concurrency_limit: number | null
  rpm_limit: number | null
  /** The local profile group whose profiles are to be onboarded. */
  scope_group: string | null
}

/** The saved onboarder token as it may be shown. The token itself never reaches the browser. */
export interface Connection {
  saved: boolean
  preview: string | null
  label: string | null
  read_at: string | null
  refused: boolean
  refused_at: string | null
  /** False when the installation has no encryption key and keeps the token as typed. */
  encrypted: boolean | null
  platform: string
}

export interface NodeOption {
  id: string
  name: string
  online: boolean
  used: number
  capacity: number
}

export interface OrgOption {
  id: string
  name: string
  domains: string[]
}

export interface PoolProxy {
  id: string
  exit_ip: string | null
  group: string | null
  /** Accounts this exit can still take. */
  slots: number
}

export interface Pool {
  node_id: string
  groups: { name: string; assignable: number }[]
  proxies: PoolProxy[]
}

export interface Options {
  label: string
  nodes: NodeOption[]
  orgs: OrgOption[]
  pool: Pool | null
}

export interface Presets {
  connection: Connection
  options: Options | null
  /** Why the options are absent although a token is saved and accepted. */
  options_error: string | null
  preset: Preset
  version: number
}

export interface ScopeGroup {
  id: string
  name: string
  pending: number
  onboarded: number
}

/**
 * An authorization asked of the platform for a row's account.
 *
 * `pending`: its link waits for the code. `working`: the platform has the
 * code. `lost`: it can no longer be asked about. Only `done` with the outcome
 * `onboarded` or `reused` puts the row's account on the platform.
 */
export interface Authorization {
  status: 'pending' | 'working' | 'done' | 'failed' | 'lost'
  outcome: 'onboarded' | 'reused' | 'mismatch' | 'failed' | null
  /** The platform's code for a failure. */
  error_code: string | null
  /** The account that was onboarded, which may not be the row's. */
  account_email: string | null
  node: string | null
  /** Where its proxy comes out, or `auto` / `auto:<group>` when the platform picks. */
  proxy: string | null
  expires_at: string | null
  cooldown_until: string | null
  created_at: string
}

export interface ScopeRow {
  profile_id: string
  name: string
  email: string
  exit_ip: string | null
  exit_checked: boolean
  exit_reachable: boolean
  onboarded: boolean
  /** Whether the signed-in user may run this profile's browser. */
  can_launch: boolean
  /** The last authorization asked for this row's address. */
  authorization: Authorization | null
}

/** How a row's authorization stands, as the platform has just said. */
export interface RowJob {
  profile_id: string
  onboarded: boolean
  authorization: Authorization | null
  /** The authorize link, only while it waits for its code. */
  authorize_url: string | null
  /** Only when a link was just made: whether it was opened in the row's browser. */
  opened: boolean | null
  /** Where: a new tab of a window the browser already had, or a new window when no tab could be had. */
  opened_in: 'tab' | 'window' | null
}

export interface CheckedRow extends ScopeRow {
  status: string
  proxy_id: string | null
}

export interface Preflight {
  ok: boolean
  version: number
  issues: { code: string; field: string }[]
  rows: CheckedRow[]
  ready: number
  blocked: number
}

export const autoOnboardingAPI = {
  connection: () => apiRequest<Connection>(`${PREFIX}/connection`),
  saveToken: (token: string) =>
    apiRequest<{ connection: Connection; replaced: boolean }>(`${PREFIX}/connection`, { method: 'PUT', body: JSON.stringify({ token }) }),
  /** Asks the platform anew each time, so this is also the refresh. */
  presets: (nodeId?: string | null) =>
    apiRequest<Presets>(`${PREFIX}/presets${nodeId ? `?node_id=${encodeURIComponent(nodeId)}` : ''}`),
  savePreset: (preset: Preset) =>
    apiRequest<{ preset: Preset; version: number }>(`${PREFIX}/presets`, { method: 'PUT', body: JSON.stringify(preset) }),
  scopes: (group?: string | null) =>
    apiRequest<{ groups: ScopeGroup[]; rows: ScopeRow[] | null }>(`${PREFIX}/scopes${group ? `?group=${encodeURIComponent(group)}` : ''}`),
  preflight: () => apiRequest<Preflight>(`${PREFIX}/preflight`, { method: 'POST' }),
  /** Makes an authorize link and opens it in the row's running browser; an open one is given back. */
  createJob: (profileId: string) => apiRequest<RowJob>(`${PREFIX}/rows/${encodeURIComponent(profileId)}/job`, { method: 'POST' }),
  /** Asks the platform anew while the authorization is open. */
  job: (profileId: string) => apiRequest<RowJob>(`${PREFIX}/rows/${encodeURIComponent(profileId)}/job`),
  submitCode: (profileId: string, code: string) =>
    apiRequest<RowJob>(`${PREFIX}/rows/${encodeURIComponent(profileId)}/job/code`, { method: 'POST', body: JSON.stringify({ code }) }),
  cancelJob: (profileId: string) => apiRequest<RowJob>(`${PREFIX}/rows/${encodeURIComponent(profileId)}/job/cancel`, { method: 'POST' }),
}
