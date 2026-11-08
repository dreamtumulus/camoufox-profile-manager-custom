import assert from 'node:assert/strict'
import test from 'node:test'
import { parseEmailList, proxyAtCapacity, shouldCloseBeforeNext } from '../src/lib/onboarding-flow'
import type { OnboardingProxy, OnboardingSession } from '../src/lib/onboarding-api'

const proxy = { boundAccounts: 0, reservedAccounts: 0 } as OnboardingProxy
const session = { profile_id: 'profile-a', status: 'submitted' } as OnboardingSession

test('proxy capacity includes both bound and reserved accounts', () => {
  assert.equal(proxyAtCapacity({ ...proxy, boundAccounts: 2 }), false)
  assert.equal(proxyAtCapacity({ ...proxy, boundAccounts: 3 }), true)
  assert.equal(proxyAtCapacity({ ...proxy, boundAccounts: 2, reservedAccounts: 1 }), true)
})

test('next-account flow closes only sessions that can still have a browser', () => {
  assert.equal(shouldCloseBeforeNext(session), true)
  assert.equal(shouldCloseBeforeNext({ ...session, status: 'completed' }), true)
  assert.equal(shouldCloseBeforeNext({ ...session, status: 'closed' }), false)
  assert.equal(shouldCloseBeforeNext({ ...session, status: 'error' }), false)
  assert.equal(shouldCloseBeforeNext({ ...session, profile_id: null }), false)
})

test('email batch accepts lines and separators, normalizes case and removes duplicates', () => {
  assert.deepEqual(parseEmailList('First@Example.com\nsecond@example.com, THIRD@example.com；first@example.com'), [
    'first@example.com', 'second@example.com', 'third@example.com',
  ])
  assert.deepEqual(parseEmailList(' \n,； '), [])
})
