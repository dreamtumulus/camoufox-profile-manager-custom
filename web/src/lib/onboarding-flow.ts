import type { ApiKeyOptions, OnboardingProxy, OnboardingSession } from './onboarding-api'

export function proxyAtCapacity(proxy: OnboardingProxy): boolean {
  return proxy.boundAccounts + proxy.reservedAccounts >= (proxy.capacity ?? 3)
}

export function apiKeyInventory(options: ApiKeyOptions) {
  const groups = options.groups.map(group => ({
    group: group.name,
    total: group.ip_count,
    live: group.ready_count,
    assignable: group.free_count,
  }))
  const proxies: OnboardingProxy[] = options.groups.flatMap(group => group.ips.filter(ip => ip.observed_ip).map(ip => ({
    id: ip.id,
    label: group.display_name,
    group: group.name,
    host: ip.server,
    port: ip.port,
    status: ip.status,
    bound: ip.accounts > 0,
    quarantined: ip.status !== 'ok',
    boundAccounts: ip.accounts,
    reservedAccounts: ip.signing_in,
    normalSlots: Math.max(0, ip.free),
    overflowSlots: 0,
    capacity: ip.capacity,
    exitIp: ip.observed_ip,
    country: null,
  })))
  return { groups, proxies }
}

export function parseEmailList(text: string): string[] {
  return [...new Set(text.split(/[\s,;，；]+/).map(value => value.trim().toLowerCase()).filter(Boolean))]
}

export type AccountOnboardingState = 'not_started' | 'in_progress' | 'ready_to_submit' | 'completed' | 'failed' | 'closed_incomplete'

export function accountOnboardingState(session?: OnboardingSession): AccountOnboardingState {
  if (!session) return 'not_started'
  if (session.status === 'error' || session.status === 'failed') return 'failed'
  if (session.job_id || session.status === 'submitted' || session.status === 'completed') return 'completed'
  if (session.status === 'extracted') return 'ready_to_submit'
  if (session.status === 'closed') return 'closed_incomplete'
  return 'in_progress'
}

export function shouldCloseBeforeNext(session: OnboardingSession): boolean {
  return !!session.profile_id && ['browser_open', 'extracted', 'submitting', 'submitted', 'completed'].includes(session.status)
}
