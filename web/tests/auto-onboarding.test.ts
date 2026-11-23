import assert from 'node:assert/strict'
import test from 'node:test'
import type { Authorization, Connection, Options, Preset, ScopeRow } from '../src/lib/auto-onboarding-api'
import {
  AUTHORIZATION_TEXTS,
  authorizationSaid,
  isOpen,
  linkBlock,
  outcomeSaid,
  proxySaid,
  refusalSaid,
  timeLeft,
} from '../src/lib/auto-onboarding-authorization'
import {
  ISSUE_MESSAGES,
  OPTIONS_ERRORS,
  ROW_MESSAGES,
  exitNote,
  parseLimit,
  reconcile,
  samePreset,
  scopeOf,
  summarize,
  tokenProblem,
  validUuid,
} from '../src/lib/auto-onboarding-preset'
import { catalogs } from '../src/lib/i18n'

const EMPTY: Preset = {
  node_id: null, proxy_mode: 'row', proxy_group: null, proxy_id: null, org_id: null, domain: null,
  org_manual: false, org_uuid: null, note: null, concurrency_limit: null, rpm_limit: null, scope_group: null,
}

const OPTIONS: Options = {
  label: 'Shift A',
  nodes: [
    { id: 'direct-1', name: 'direct-1', online: true, used: 13, capacity: 20 },
    { id: 'direct-2', name: 'direct-2', online: true, used: 4, capacity: 20 },
  ],
  orgs: [
    { id: 'org-a', name: 'Team A', domains: ['example.com'] },
    { id: 'org-b', name: 'Team B', domains: ['example.com', 'example.net'] },
    { id: 'org-c', name: 'Team C', domains: [] },
  ],
  pool: {
    node_id: 'direct-1',
    groups: [{ name: 'UK', assignable: 12 }],
    proxies: [
      { id: 'p18', exit_ip: '203.0.113.18', group: 'UK', slots: 3 },
      { id: 'p17', exit_ip: '203.0.113.17', group: 'UK', slots: 3 },
      { id: 'p17b', exit_ip: '203.0.113.17', group: 'UK', slots: 1 },
    ],
  },
}

const CHOSEN: Preset = { ...EMPTY, node_id: 'direct-1', org_id: 'org-a', domain: 'example.com' }

test('a typed token is sent only when it could be an onboarder token', () => {
  assert.equal(tokenProblem('   '), 'empty')
  assert.equal(tokenProblem('sk-ant-something'), 'malformed')
  assert.equal(tokenProblem('1dk_onb_with space'), 'malformed')
  assert.equal(tokenProblem('  1dk_onb_demo-only\n'), null)
})

test('a limit is empty or a whole number in the platform range', () => {
  assert.deepEqual(parseLimit(' '), { ok: true, value: null })
  assert.deepEqual(parseLimit('0'), { ok: true, value: 0 })
  assert.deepEqual(parseLimit('100000'), { ok: true, value: 100000 })
  for (const text of ['-1', '1.5', '100001', '2e3', 'ten']) assert.deepEqual(parseLimit(text), { ok: false }, text)
})

test('an organization UUID is told by its shape, in either case', () => {
  assert.equal(validUuid(' 0A1B2C3D-0000-4000-8000-000000000000 '), true)
  assert.equal(validUuid('not-a-uuid'), false)
})

test('choices not made yet take the first on offer, without a word', () => {
  const { preset, changes } = reconcile(EMPTY, OPTIONS)

  assert.deepEqual(changes, [])
  assert.equal(preset.node_id, 'direct-1')
  assert.equal(preset.org_id, 'org-a')
  assert.equal(preset.domain, 'example.com', 'a single domain is chosen with its organization')
})

test('choices still on offer are kept as they are', () => {
  const kept = { ...CHOSEN, proxy_mode: 'pool' as const, proxy_id: 'p18', org_id: 'org-b', domain: 'Example.NET' }

  const { preset, changes } = reconcile(kept, OPTIONS)

  assert.deepEqual(changes, [])
  assert.deepEqual(preset, { ...kept, domain: 'example.net' })
})

test('a node that is gone takes its proxy group and its proxy with it, and says so', () => {
  const gone = { ...OPTIONS, nodes: [OPTIONS.nodes[1]], pool: { node_id: 'direct-2', groups: [], proxies: [] } }

  const auto = reconcile({ ...CHOSEN, proxy_mode: 'auto', proxy_group: 'UK' }, gone)
  assert.deepEqual(auto.changes, [{ kind: 'node', from: 'direct-1', to: 'direct-2' }, { kind: 'proxy_group', from: 'UK' }])
  assert.deepEqual([auto.preset.node_id, auto.preset.proxy_group], ['direct-2', null])

  const pool = reconcile({ ...CHOSEN, proxy_mode: 'pool', proxy_id: 'p18' }, gone)
  assert.deepEqual(pool.changes, [{ kind: 'node', from: 'direct-1', to: 'direct-2' }, { kind: 'proxy' }])
  assert.equal(pool.preset.proxy_id, null)

  // What was chosen under another way of picking is dropped without a word.
  const row = reconcile({ ...CHOSEN, proxy_group: 'UK', proxy_id: 'p18' }, gone)
  assert.deepEqual(row.changes, [{ kind: 'node', from: 'direct-1', to: 'direct-2' }])
})

test('a proxy or a group that left the pool is dropped, and said when it was in use', () => {
  const pool = reconcile({ ...CHOSEN, proxy_mode: 'pool', proxy_id: 'gone' }, OPTIONS)
  assert.deepEqual(pool.changes, [{ kind: 'proxy' }])
  assert.equal(pool.preset.proxy_id, null)

  const group = reconcile({ ...CHOSEN, proxy_mode: 'auto', proxy_group: 'US' }, OPTIONS)
  assert.deepEqual(group.changes, [{ kind: 'proxy_group', from: 'US' }])
})

test('the pool of another node says nothing about the chosen one', () => {
  const other = { ...CHOSEN, node_id: 'direct-2', proxy_mode: 'pool' as const, proxy_id: 'p2-61' }

  assert.deepEqual(reconcile(other, OPTIONS), { preset: other, changes: [] })
})

test('an organization that is gone is replaced by the first left, and its domain asked again', () => {
  const without = { ...OPTIONS, orgs: OPTIONS.orgs.slice(1) }

  const { preset, changes } = reconcile(CHOSEN, without)

  assert.deepEqual(changes, [{ kind: 'org', to: 'Team B', pickDomain: true }])
  assert.deepEqual([preset.org_id, preset.domain], ['org-b', null])
})

test('a domain the organization no longer has is asked again', () => {
  const { preset, changes } = reconcile({ ...CHOSEN, org_id: 'org-b', domain: 'example.org' }, OPTIONS)

  assert.deepEqual(changes, [{ kind: 'domain' }])
  assert.equal(preset.domain, null)
})

test('a token without organization profiles keeps no profile', () => {
  const { preset, changes } = reconcile({ ...CHOSEN, org_manual: true, org_uuid: 'typed' }, { ...OPTIONS, orgs: [] })

  assert.deepEqual(changes, [])
  assert.deepEqual([preset.org_id, preset.domain, preset.org_manual, preset.org_uuid], [null, null, true, 'typed'])
})

test('the group to onboard is the chosen one while it has profiles left', () => {
  const groups = [{ id: 'g2', name: 'B', pending: 1, onboarded: 0 }, { id: 'g1', name: 'A', pending: 2, onboarded: 0 }]

  assert.equal(scopeOf({ ...EMPTY, scope_group: 'g1' }, groups), 'g1')
  assert.equal(scopeOf({ ...EMPTY, scope_group: 'gone' }, groups), 'g2')
  assert.equal(scopeOf(EMPTY, groups), 'g2')
  assert.equal(scopeOf({ ...EMPTY, scope_group: 'g1' }, []), null)
})

test('two presets are the same when every choice is', () => {
  assert.equal(samePreset(CHOSEN, { ...CHOSEN }), true)
  assert.equal(samePreset(CHOSEN, { ...CHOSEN, rpm_limit: 0 }), false)
})

test('the last line of the card says what is chosen', () => {
  assert.deepEqual(summarize(CHOSEN, OPTIONS), {
    node: 'direct-1',
    proxy: { kind: 'row' },
    org: { kind: 'profile', name: 'Team A', domain: 'example.com', needsDomain: false },
  })
  assert.deepEqual(summarize({ ...CHOSEN, proxy_mode: 'auto', proxy_group: 'UK' }, OPTIONS).proxy, { kind: 'auto', group: 'UK' })
  assert.deepEqual(summarize({ ...CHOSEN, proxy_mode: 'pool' }, OPTIONS).proxy, { kind: 'pool', exitIp: null })
  assert.deepEqual(summarize({ ...CHOSEN, proxy_mode: 'pool', proxy_id: 'p18' }, OPTIONS).proxy, { kind: 'pool', exitIp: '203.0.113.18' })
  assert.deepEqual(summarize({ ...CHOSEN, org_id: 'org-b', domain: null }, OPTIONS).org, { kind: 'profile', name: 'Team B', domain: null, needsDomain: true })
  assert.deepEqual(summarize({ ...CHOSEN, org_id: 'org-c', domain: null }, OPTIONS).org, { kind: 'profile', name: 'Team C', domain: null, needsDomain: false })

  const bare = { ...OPTIONS, orgs: [] }
  assert.deepEqual(summarize(CHOSEN, bare).org, { kind: 'unspecified' })
  assert.deepEqual(summarize({ ...CHOSEN, org_manual: true }, bare).org, { kind: 'uuid', short: null })
  assert.deepEqual(summarize({ ...CHOSEN, org_manual: true, org_uuid: '0a1b2c3d-0000-4000-8000-000000000000' }, bare).org, { kind: 'uuid', short: '0a1b2c3d…' })
})

function row(exit: string | null, values: Partial<ScopeRow> = {}): ScopeRow {
  return { profile_id: 'p', name: 'UK', email: 'user@example.com', exit_ip: exit, exit_checked: true, exit_reachable: true, onboarded: false, can_launch: true, authorization: null, ...values }
}

test('a row says how it will be onboarded before the server checks', () => {
  assert.deepEqual(exitNote(row('203.0.113.18'), CHOSEN, OPTIONS), { kind: 'matched' })
  assert.deepEqual(exitNote(row('203.0.113.17'), CHOSEN, OPTIONS), { kind: 'ambiguous' })
  assert.deepEqual(exitNote(row('203.0.113.16'), CHOSEN, OPTIONS), { kind: 'not_in_pool' })
  assert.deepEqual(exitNote(row(null, { exit_checked: false, exit_reachable: false }), CHOSEN, OPTIONS), { kind: 'unknown' })
  assert.deepEqual(exitNote(row(null, { exit_reachable: false }), CHOSEN, OPTIONS), { kind: 'failed' })
  assert.deepEqual(exitNote(row('203.0.113.16'), { ...CHOSEN, proxy_mode: 'auto' }, OPTIONS), { kind: 'platform' })
  assert.deepEqual(exitNote(row('203.0.113.16'), { ...CHOSEN, proxy_mode: 'pool' }, OPTIONS), { kind: 'pool', exitIp: null })
  assert.deepEqual(exitNote(row('203.0.113.16'), { ...CHOSEN, proxy_mode: 'pool', proxy_id: 'p18' }, OPTIONS), { kind: 'pool', exitIp: '203.0.113.18' })
  assert.deepEqual(exitNote(row('203.0.113.18'), CHOSEN, null), { kind: 'no_options' })
})

test('every message the server can ask for by code is in every catalog', () => {
  const messages = [...Object.values(ISSUE_MESSAGES), ...Object.values(ROW_MESSAGES), ...Object.values(OPTIONS_ERRORS), ...AUTHORIZATION_TEXTS]
  for (const [locale, catalog] of Object.entries(catalogs)) {
    for (const message of messages) assert.ok(Object.hasOwn(catalog, message), `${locale}: ${message}`)
  }
})

// --- the authorization of a row --------------------------------------------------

function authorization(values: Partial<Authorization> = {}): Authorization {
  return { status: 'pending', outcome: null, error_code: null, account_email: null, node: 'direct-1', proxy: '203.0.113.18', expires_at: '2030-01-01T00:30:00Z', cooldown_until: null, created_at: '2030-01-01T00:00:00Z', ...values }
}

const SAVED: Connection = { saved: true, preview: '1dk_onb_demo…only', label: 'Shift A', read_at: null, refused: false, refused_at: null, encrypted: true, platform: 'https://1dayapi.com' }

test('a row says why it cannot be given a link, the first reason first', () => {
  const state = { running: true, editingToken: false }
  assert.equal(linkBlock(row('203.0.113.18'), SAVED, state), null)
  assert.equal(linkBlock(row('203.0.113.18'), SAVED, { ...state, running: false }), 'Run the browser first')
  assert.equal(linkBlock(row('203.0.113.18', { can_launch: false }), SAVED, state), 'No permission to run this profile')
  assert.equal(linkBlock(row('203.0.113.18'), SAVED, { ...state, editingToken: true }), 'The token is being changed')
  assert.equal(linkBlock(row('203.0.113.18'), { ...SAVED, refused: true }, state), 'Token invalid')
  assert.equal(linkBlock(row('203.0.113.18'), { ...SAVED, saved: false }, state), 'No token saved yet')
  assert.equal(linkBlock(row('203.0.113.18', { onboarded: true }), { ...SAVED, saved: false }, { running: false, editingToken: true }), 'Already onboarded')
})

test('an open authorization is said with the node and proxy it was made with', () => {
  const waiting = authorizationSaid(row('203.0.113.18', { authorization: authorization() }), false)
  assert.deepEqual([waiting.tone, waiting.text], ['warn', 'Waiting for the authorization code'])
  assert.deepEqual(waiting.made, { node: 'direct-1', proxy: { text: '{exit}', values: { exit: '203.0.113.18' } } })

  const working = authorizationSaid(row('203.0.113.18', { authorization: authorization({ status: 'working', proxy: 'auto:UK' }) }), false)
  assert.deepEqual([working.tone, working.text], ['busy', 'The platform is working on it'])
  assert.deepEqual(working.made?.proxy, { text: 'assigned within group {group}', values: { group: 'UK' } })
  assert.deepEqual(proxySaid('auto'), { text: 'assigned by the platform' })

  const unreachable = authorizationSaid(row('203.0.113.18', { authorization: authorization() }), true)
  assert.deepEqual([unreachable.tone, unreachable.text], ['danger', 'Cannot be asked about · token invalid'])
  assert.equal(isOpen(authorization()) && isOpen(authorization({ status: 'working' })), true)
  assert.equal(isOpen(authorization({ status: 'done' })) || isOpen(authorization({ status: 'lost' })) || isOpen(null), false)
})

test('only done for the row’s own account is said as onboarded', () => {
  const done = outcomeSaid(authorization({ status: 'done', outcome: 'onboarded', account_email: 'user@example.com' }), 'user@example.com')
  assert.deepEqual([done.tone, done.text, done.noteValues], ['ok', 'Onboarded', { email: 'user@example.com' }])

  const resting = outcomeSaid(authorization({ status: 'done', outcome: 'onboarded', cooldown_until: '2030-01-01T04:00:00Z' }), 'user@example.com', () => '04:00')
  assert.deepEqual(resting.noteValues, { email: 'user@example.com', time: '04:00' })

  const again = outcomeSaid(authorization({ status: 'done', outcome: 'reused' }), 'user@example.com')
  assert.deepEqual([again.tone, again.text], ['ok', 'The platform already had this account and authorized it again'])

  const other = outcomeSaid(authorization({ status: 'done', outcome: 'mismatch', account_email: 'another@example.com' }), 'user@example.com')
  assert.deepEqual([other.tone, other.text], ['danger', 'The account onboarded is not this row’s'])
  assert.deepEqual(other.noteValues, { account: 'another@example.com', email: 'user@example.com' })
})

test('a failure is said by the platform’s code, and an unknown code is shown as it is', () => {
  const said = (code: string) => outcomeSaid(authorization({ status: 'failed', outcome: 'failed', error_code: code }), 'user@example.com')
  assert.deepEqual([said('account_exists').tone, said('account_exists').text], ['warn', 'The platform already has this account, to be looked into'])
  assert.equal(said('domain_mismatch').text, 'The account’s address is not under the chosen domain')
  assert.equal(said('invalid_grant').text, said('token_exchange_failed').text)
  assert.deepEqual([said('callback_expired').tone, said('callback_expired').text], ['warn', 'The authorization lapsed'])
  assert.deepEqual([said('job_cancelled').tone, said('job_cancelled').text], ['dim', 'Cancelled'])
  assert.deepEqual([said('something_new').text, said('something_new').values], ['The authorization failed ({code})', { code: 'something_new' }])
  assert.equal(outcomeSaid(authorization({ status: 'lost' }), 'user@example.com').text, 'This authorization can no longer be asked about')
})

test('a row without an authorization says whether it was onboarded before', () => {
  assert.equal(authorizationSaid(row('203.0.113.18'), false).text, 'No link made yet')
  assert.equal(authorizationSaid(row('203.0.113.18', { onboarded: true }), false).text, 'Already onboarded')
})

test('the time left is the platform’s own, never below zero', () => {
  const at = Date.parse('2030-01-01T00:00:00Z')
  assert.equal(timeLeft('2030-01-01T00:30:00Z', at), '30:00')
  assert.equal(timeLeft('2030-01-01T00:30:00Z', at + 61_500), '28:58')
  assert.equal(timeLeft('2030-01-01T00:30:00Z', at + 3_600_000), '00:00')
  assert.equal(timeLeft(null, at), null)
  assert.equal(timeLeft('not a time', at), null)
})

test('a refusal is put into words by its code', () => {
  assert.equal(refusalSaid('browser_not_running', 'x').text, 'Run this row’s browser first: the link is opened in it.')
  assert.equal(refusalSaid('proxy_missing', 'x').text, 'Pick a proxy from the pool first.')
  assert.equal(refusalSaid('exit_not_in_pool', 'x').text, 'Exit is not in the node’s pool, or is full')
  assert.match(refusalSaid('platform_proxy_full', 'x').text, /no room for another account/)
  assert.deepEqual(refusalSaid('platform_invalid_request', 'name is too long (max 80)').values, { message: 'name is too long (max 80)' })
  assert.deepEqual(refusalSaid('platform_something_new', 'told so'), { text: 'The platform refused ({code}): {message}', values: { code: 'something_new', message: 'told so' } })
  assert.deepEqual(refusalSaid(undefined, 'Network error'), { text: '{message}', values: { message: 'Network error' } })
})
