/**
 * The access preset card's rules, kept apart from the screen so they can be tested.
 *
 * The server judges the saved choices on its own when asked to check them;
 * what is here keeps the screen honest in between: it drops a choice the
 * platform no longer offers, and says so.
 */
import type { Options, Pool, Preset, ScopeGroup, ScopeRow } from './auto-onboarding-api'

export const TOKEN_PREFIX = '1dk_onb_'
/** The platform's own bound for both limits. */
export const LIMIT_MAX = 100000

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Why a typed token is not worth sending, if it is not. */
export function tokenProblem(text: string): 'empty' | 'malformed' | null {
  const value = text.trim()
  if (!value) return 'empty'
  return value.startsWith(TOKEN_PREFIX) && !/\s/.test(value) ? null : 'malformed'
}

export function validUuid(text: string): boolean {
  return UUID.test(text.trim())
}

/** A limit as typed: empty leaves it to the platform, otherwise a whole number in its range. */
export function parseLimit(text: string): { ok: true; value: number | null } | { ok: false } {
  const value = text.trim()
  if (value === '') return { ok: true, value: null }
  if (!/^\d+$/.test(value) || Number(value) > LIMIT_MAX) return { ok: false }
  return { ok: true, value: Number(value) }
}

/** A choice the platform no longer offers, and what took its place. */
export type Change =
  | { kind: 'node'; from: string; to: string | null }
  | { kind: 'proxy_group'; from: string }
  | { kind: 'proxy' }
  | { kind: 'org'; to: string; pickDomain: boolean }
  | { kind: 'domain' }

/** The pool belongs to one node; it says nothing about another. */
function poolOf(preset: Preset, options: Options): Pool | null {
  return options.pool && options.pool.node_id === preset.node_id ? options.pool : null
}

/**
 * Bring the saved choices in line with what the token may use now.
 *
 * A choice not made yet takes the first thing on offer, silently. A choice
 * that was made and is gone goes back to the first one left, and is reported,
 * because it changes where accounts will go.
 */
export function reconcile(preset: Preset, options: Options): { preset: Preset; changes: Change[] } {
  const next = { ...preset }
  const changes: Change[] = []

  if (!options.nodes.some(node => node.id === next.node_id)) {
    const first = options.nodes[0]?.id ?? null
    if (next.node_id !== null) {
      changes.push({ kind: 'node', from: next.node_id, to: first })
      // A proxy group and a pool proxy belong to the node that is gone.
      if (next.proxy_mode === 'auto' && next.proxy_group) changes.push({ kind: 'proxy_group', from: next.proxy_group })
      if (next.proxy_mode === 'pool' && next.proxy_id) changes.push({ kind: 'proxy' })
      next.proxy_group = null
      next.proxy_id = null
    }
    next.node_id = first
  }

  const pool = poolOf(next, options)
  if (pool) {
    if (next.proxy_group && !pool.groups.some(group => group.name === next.proxy_group)) {
      if (next.proxy_mode === 'auto') changes.push({ kind: 'proxy_group', from: next.proxy_group })
      next.proxy_group = null
    }
    if (next.proxy_id && !pool.proxies.some(proxy => proxy.id === next.proxy_id)) {
      if (next.proxy_mode === 'pool') changes.push({ kind: 'proxy' })
      next.proxy_id = null
    }
  }

  if (options.orgs.length) {
    let org = options.orgs.find(item => item.id === next.org_id)
    const replaced = !org && next.org_id !== null
    if (!org) {
      org = options.orgs[0]
      next.org_id = org.id
      next.domain = null
    }
    const domains = org.domains
    const kept = domains.find(domain => domain.toLowerCase() === (next.domain ?? '').toLowerCase())
    const lost = next.domain !== null && !kept
    next.domain = kept ?? (domains.length === 1 ? domains[0] : null)
    if (replaced) changes.push({ kind: 'org', to: org.name, pickDomain: domains.length > 1 })
    else if (lost) changes.push({ kind: 'domain' })
  } else {
    next.org_id = null
    next.domain = null
  }
  return { preset: next, changes }
}

/** The group to onboard: the chosen one while it has profiles left, else the first that has. */
export function scopeOf(preset: Preset, groups: ScopeGroup[]): string | null {
  return groups.some(group => group.id === preset.scope_group) ? preset.scope_group : groups[0]?.id ?? null
}

export function samePreset(a: Preset, b: Preset): boolean {
  return (Object.keys(a) as (keyof Preset)[]).every(key => a[key] === b[key])
}

/** What the card's last line says is chosen, for the screen to put into words. */
export interface Summary {
  node: string | null
  proxy:
    | { kind: 'row' }
    | { kind: 'auto'; group: string | null }
    | { kind: 'pool'; exitIp: string | null }
  org:
    | { kind: 'profile'; name: string; domain: string | null; needsDomain: boolean }
    | { kind: 'unspecified' }
    | { kind: 'uuid'; short: string | null }
    | { kind: 'none' }
}

export function summarize(preset: Preset, options: Options): Summary {
  const node = options.nodes.find(item => item.id === preset.node_id)
  const pool = poolOf(preset, options)
  const picked = pool?.proxies.find(proxy => proxy.id === preset.proxy_id)
  const org = options.orgs.find(item => item.id === preset.org_id)
  const uuid = preset.org_uuid?.trim() ?? ''
  return {
    node: node?.name ?? null,
    proxy:
      preset.proxy_mode === 'auto' ? { kind: 'auto', group: preset.proxy_group }
      : preset.proxy_mode === 'pool' ? { kind: 'pool', exitIp: picked ? picked.exit_ip ?? picked.id : null }
      : { kind: 'row' },
    org:
      options.orgs.length
        ? org
          ? { kind: 'profile', name: org.name, domain: preset.domain, needsDomain: org.domains.length > 0 && !preset.domain }
          : { kind: 'none' }
        : preset.org_manual
          ? { kind: 'uuid', short: uuid ? `${uuid.slice(0, 8)}…` : null }
          : { kind: 'unspecified' },
  }
}

/** How a row will be onboarded, as far as the screen can tell before the server checks. */
export type ExitNote =
  | { kind: 'matched' }
  | { kind: 'unknown' }
  | { kind: 'failed' }
  | { kind: 'not_in_pool' }
  | { kind: 'ambiguous' }
  | { kind: 'platform' }
  | { kind: 'pool'; exitIp: string | null }
  | { kind: 'no_options' }

export function exitNote(row: ScopeRow, preset: Preset, options: Options | null): ExitNote {
  if (!options) return { kind: 'no_options' }
  if (preset.proxy_mode === 'auto') return { kind: 'platform' }
  const pool = poolOf(preset, options)
  if (preset.proxy_mode === 'pool') {
    const picked = pool?.proxies.find(proxy => proxy.id === preset.proxy_id)
    return { kind: 'pool', exitIp: picked ? picked.exit_ip ?? picked.id : null }
  }
  if (!row.exit_checked) return { kind: 'unknown' }
  if (!row.exit_reachable || !row.exit_ip) return { kind: 'failed' }
  const exit = row.exit_ip.toLowerCase()
  const matches = (pool?.proxies ?? []).filter(proxy => (proxy.exit_ip ?? '').toLowerCase() === exit).length
  return { kind: matches === 1 ? 'matched' : matches ? 'ambiguous' : 'not_in_pool' }
}

/** What the check found missing or no longer on offer, by the server's code. */
export const ISSUE_MESSAGES: Record<string, string> = {
  node_missing: 'Choose a node.',
  node_unavailable: 'The chosen node is no longer among the nodes this token may use.',
  node_offline: 'The chosen node is offline.',
  proxy_group_unavailable: 'The chosen proxy group is not on this node.',
  proxy_missing: 'Pick a proxy from the pool first.',
  proxy_unavailable: 'The chosen proxy can no longer take an account. Pick another.',
  org_missing: 'Choose an organization.',
  org_unavailable: 'The chosen organization is no longer among this token’s organization profiles.',
  domain_missing: 'Choose an email domain for the organization first.',
  domain_unavailable: 'The chosen email domain is not one of the organization’s.',
  org_uuid_missing: 'Enter the organization UUID, or set the organization to “Unspecified”.',
  org_uuid_invalid: 'An organization UUID looks like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.',
  scope_missing: 'Choose the group to onboard.',
  scope_unavailable: 'The chosen group has no profile left to onboard.',
  no_ready_rows: 'With these choices none of the group’s profiles would be onboarded. See the reason on each row.',
}

/** What stands in a row's way, by the server's code. `ready` is not in anyone's way. */
export const ROW_MESSAGES: Record<string, string> = {
  ready: 'Would be onboarded',
  onboarded: 'Already onboarded',
  domain_mismatch: 'Address is not under the chosen domain',
  exit_unknown: 'Proxy never checked, exit unknown',
  exit_failed: 'Last proxy check failed',
  exit_not_in_pool: 'Exit is not in the node’s pool, or is full',
  exit_ambiguous: 'Several pool proxies come out at this exit',
}

/** Why the platform's options could not be read, by the server's code. */
export const OPTIONS_ERRORS: Record<string, string> = {
  oneday_unavailable: 'The platform could not be reached. The saved choices are shown as they were; refresh to try again.',
  default: 'The platform did not answer as expected. The saved choices are shown as they were; refresh to try again.',
}
