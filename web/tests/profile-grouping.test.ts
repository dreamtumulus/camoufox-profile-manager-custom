import assert from 'node:assert/strict'
import test from 'node:test'
import { AUTO_EXPAND_LIMIT, UNGROUPED, buildSections, opensByItself, parseExpanded, parseLayout } from '../src/lib/profile-grouping'

const check = (reachable: boolean) => ({ checked_at: '2026-11-01T00:00:00Z', reachable, error: reachable ? null : 'timeout', latency_ms: reachable ? 120 : null, ip: null, country: null, timezone: null, findings: [] })
const row = (id: string, group: string | null, extra: { status?: string; last_used?: string | null; proxy_check?: ReturnType<typeof check> | null } = {}) => ({ id, group, status: 'active', last_used: null, proxy_check: null, ...extra })
const names = new Map([['g9', 'Homes 9'], ['g10', 'Homes 10'], ['g2', 'Homes 2']])

test('groups are ordered the way their numbers count, with ungrouped profiles last', () => {
  const rows = [row('a', 'g10'), row('b', null), row('c', 'g9'), row('d', 'g2'), row('e', 'g9')]
  const sections = buildSections(rows, rows, names, new Set())
  assert.deepEqual(sections.map(section => section.name), ['Homes 2', 'Homes 9', 'Homes 10', null])
  assert.deepEqual(sections.map(section => section.key), ['g2', 'g9', 'g10', UNGROUPED])
  assert.deepEqual(sections[1].rows.map(item => item.id), ['c', 'e'])
  const reversed = buildSections(rows, rows, names, new Set(), { descending: true })
  assert.deepEqual(reversed.map(section => section.name), ['Homes 10', 'Homes 9', 'Homes 2', null])
})

test('a section counts its whole group while listing only the matching profiles', () => {
  const all = [
    row('a', 'g9', { last_used: '2026-11-20T10:00:00Z' }),
    row('b', 'g9', { status: 'blocked', last_used: '2026-11-25T10:00:00Z', proxy_check: check(false) }),
    row('c', 'g9', { proxy_check: check(true) }),
    row('d', 'g2'),
  ]
  const [section, ...rest] = buildSections([all[0]], all, names, new Set(['b', 'c', 'd']))
  assert.equal(rest.length, 0, 'a group without a match has no section')
  assert.deepEqual(section.rows.map(item => item.id), ['a'])
  assert.deepEqual(
    { total: section.total, running: section.running, blocked: section.blocked, proxyIssues: section.proxyIssues, lastUsed: section.lastUsed },
    { total: 3, running: 2, blocked: 1, proxyIssues: 1, lastUsed: '2026-11-25T10:00:00Z' },
  )
})

test('a deleted group keeps its profiles together under its ID', () => {
  const rows = [row('a', 'gone'), row('b', 'gone'), row('c', '')]
  const sections = buildSections(rows, rows, names, new Set())
  assert.deepEqual(sections.map(section => [section.name, section.total]), [['gone', 2], [null, 1]])
})

test('sections open by themselves for a lone group or a short filtered answer', () => {
  assert.equal(opensByItself(1, 5000, false), true)
  assert.equal(opensByItself(12, 109, false), false)
  assert.equal(opensByItself(12, 3, true), true)
  assert.equal(opensByItself(12, AUTO_EXPAND_LIMIT, true), true)
  assert.equal(opensByItself(12, AUTO_EXPAND_LIMIT + 1, true), false)
})

test('stored preferences fall back to the grouped, folded start', () => {
  assert.equal(parseLayout(null), 'grouped')
  assert.equal(parseLayout('list'), 'list')
  assert.equal(parseLayout('table'), 'grouped')
  assert.deepEqual(parseExpanded(null), [])
  assert.deepEqual(parseExpanded('["g9","__ungrouped__"]'), ['g9', UNGROUPED])
  assert.deepEqual(parseExpanded('{"g9":true}'), [])
  assert.deepEqual(parseExpanded('["g9", 3, null]'), ['g9'])
  assert.deepEqual(parseExpanded('not json'), [])
})
