import assert from 'node:assert/strict'
import test from 'node:test'
import { assignEmails, emailKey, parseEmailLines } from '../src/lib/email-import'

test('one address per line; unusable lines are reported by number and left out', () => {
  const parsed = parseEmailLines([
    ' First@Example.com ',
    '',
    'not-an-email',
    'second@example.com',
    'FIRST@example.com',
    'two words@example.com',
    'a@b',
    '\tthird@example.com\r',
  ].join('\n'))
  assert.equal(parsed.lines, 7)
  assert.deepEqual(parsed.emails, ['First@Example.com', 'second@example.com', 'third@example.com'])
  assert.deepEqual(parsed.problems.map(({ line, reason }) => [line, reason]), [[3, 'invalid'], [5, 'repeated'], [6, 'invalid'], [7, 'invalid']])
})

test('an address a profile already holds is refused whatever its capitals', () => {
  const holders = new Map([[emailKey('Held@Example.com'), { name: 'Account 001', inTrash: false }], [emailKey('gone@example.com'), { name: 'Old', inTrash: true }]])
  const parsed = parseEmailLines('HELD@example.com\nfree@example.com\ngone@example.com\nheld@example.com', holders)
  assert.deepEqual(parsed.emails, ['free@example.com'])
  assert.deepEqual(parsed.problems, [
    { line: 1, email: 'HELD@example.com', reason: 'taken', holder: 'Account 001', inTrash: false },
    { line: 3, email: 'gone@example.com', reason: 'taken', holder: 'Old', inTrash: true },
    { line: 4, email: 'held@example.com', reason: 'taken', holder: 'Account 001', inTrash: false },
  ])
})

test('overlong and control-character addresses are not addresses', () => {
  assert.deepEqual(parseEmailLines(`${'x'.repeat(250)}@example.com`).emails, [])
  assert.deepEqual(parseEmailLines('hid​den@example.com').emails, [])
  assert.deepEqual(parseEmailLines('').problems, [])
  assert.equal(parseEmailLines(' \n\n ').lines, 0)
})

test('a profile whose proxy did not pass is stepped over without using an address up', () => {
  const targets = ['p1', 'p2', 'p3', 'p4', 'p5']
  const { assigned, unused } = assignEmails(targets, new Set(['p1', 'p3', 'p4']), ['a@x.io', 'b@x.io', 'c@x.io', 'd@x.io'])
  assert.deepEqual([...assigned], [['p1', 'a@x.io'], ['p3', 'b@x.io'], ['p4', 'c@x.io']])
  assert.deepEqual(unused, ['d@x.io'])
})

test('fewer addresses than passing profiles fills the first ones in list order', () => {
  const { assigned, unused } = assignEmails(['p1', 'p2', 'p3'], new Set(['p3', 'p2', 'p1']), ['a@x.io'])
  assert.deepEqual([...assigned], [['p1', 'a@x.io']])
  assert.deepEqual(unused, [])
  assert.deepEqual(assignEmails(['p1'], new Set(), ['a@x.io']), { assigned: new Map(), unused: ['a@x.io'] })
  assert.deepEqual([...assignEmails(['p1', 'p1'], new Set(['p1']), ['a@x.io', 'b@x.io']).assigned], [['p1', 'a@x.io']])
})
