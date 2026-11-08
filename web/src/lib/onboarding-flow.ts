import type { OnboardingProxy, OnboardingSession } from './onboarding-api'

export function proxyAtCapacity(proxy: OnboardingProxy): boolean {
  return proxy.boundAccounts + proxy.reservedAccounts >= 3
}

export function parseEmailList(text: string): string[] {
  return [...new Set(text.split(/[\s,;，；]+/).map(value => value.trim().toLowerCase()).filter(Boolean))]
}

export function shouldCloseBeforeNext(session: OnboardingSession): boolean {
  return !!session.profile_id && ['browser_open', 'extracted', 'submitting', 'submitted', 'completed'].includes(session.status)
}
