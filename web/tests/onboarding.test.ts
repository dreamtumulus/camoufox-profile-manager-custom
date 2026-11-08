import assert from 'node:assert/strict'
import test from 'node:test'
import { accountOnboardingState, apiKeyInventory, parseEmailList, proxyAtCapacity, shouldCloseBeforeNext } from '../src/lib/onboarding-flow'
import type { ApiKeyOptions, OnboardingProxy, OnboardingSession } from '../src/lib/onboarding-api'

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

test('account states distinguish unfinished work from completed onboarding', () => {
  assert.equal(accountOnboardingState(), 'not_started')
  assert.equal(accountOnboardingState({ ...session, status: 'browser_open', job_id: null }), 'in_progress')
  assert.equal(accountOnboardingState({ ...session, status: 'extracted', job_id: null }), 'ready_to_submit')
  assert.equal(accountOnboardingState({ ...session, status: 'closed', job_id: null }), 'closed_incomplete')
  assert.equal(accountOnboardingState({ ...session, status: 'closed', job_id: 'job-a' }), 'completed')
  assert.equal(accountOnboardingState({ ...session, status: 'completed', job_id: 'job-a' }), 'completed')
  assert.equal(accountOnboardingState({ ...session, status: 'failed', job_id: 'job-a' }), 'failed')
})

test('api-key inventory preserves its own IP capacity and maps groups', () => {
  const options = {
    me: { org_id: 'org-a', org_name: 'Org A' },
    defaults: { seats: 500, rpm: 500, pool_tags: [], required_traits: [], forbidden_traits: [] },
    groups: [{ name: 'group-a', display_name: 'Group A', ip_count: 1, ready_count: 1, free_count: 1, ips: [{
      id: 'ip-a', server: '127.0.0.1', port: 8080, status: 'ok', accounts: 2,
      signing_in: 1, capacity: 4, free: 1, observed_ip: '10.0.0.1',
    }] }],
  } as ApiKeyOptions
  const inventory = apiKeyInventory(options)
  assert.equal(inventory.groups[0].group, 'group-a')
  assert.equal(inventory.proxies[0].exitIp, '10.0.0.1')
  assert.equal(inventory.proxies[0].capacity, 4)
  assert.equal(proxyAtCapacity(inventory.proxies[0]), false)
  assert.equal(proxyAtCapacity({ ...inventory.proxies[0], boundAccounts: 3 }), true)
})
