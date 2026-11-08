import { apiRequest } from './api'

const PREFIX = '/api/v1/onboarding'

export interface OnboardingSelection {
  node_id: string
  node_name: string
  org_id: string
  org_name: string
  proxy_group: string | null
  proxy_id: string
  exit_ip: string
}

export interface OnboardingNode {
  nodeId: string
  name: string | null
  online: boolean
  capacity: number
  used: number
  freeProxies: number
}

export interface OnboardingOrg {
  id: string
  name: string
  note: string | null
  status: string
  domains: string[]
  accounts: number
}

export interface OnboardingProxy {
  id: string
  label: string | null
  group: string | null
  host: string
  port: number
  status: string
  bound: boolean
  quarantined: boolean
  boundAccounts: number
  reservedAccounts: number
  normalSlots: number
  overflowSlots: number
  exitIp: string
  country: string | null
}

export interface OnboardingAccount {
  id: string
  email: string
  org_id: string
  org_name: string
  created_at: string
}

export interface OnboardingSession {
  id: string
  account_id: string
  email: string
  selection: OnboardingSelection
  profile_id: string | null
  status: string
  sk_masked: string | null
  job_id: string | null
  result: unknown
  error: string | null
  created_at: string
  updated_at: string
}

export interface SharedState {
  preferences: Partial<OnboardingSelection>
  accounts: OnboardingAccount[]
  sessions: OnboardingSession[]
  credential_configured: boolean
}

export interface OneDayOptions {
  label: string
  node: OnboardingNode
  nodes: OnboardingNode[]
  anyNode: boolean
  orgs: OnboardingOrg[]
}

export interface OnboardingConfig {
  configured: boolean
  server_url: string | null
  message?: string
}

export const onboardingAPI = {
  config: () => apiRequest<OnboardingConfig>(`${PREFIX}/config`),
  saveConfig: (body: { server_url: string; server_token: string; credential_token?: string }) =>
    apiRequest<OnboardingConfig>(`${PREFIX}/config`, { method: 'PUT', body: JSON.stringify(body) }),
  bootstrap: () => apiRequest<{ shared: SharedState; oneDay: OneDayOptions }>(`${PREFIX}/bootstrap`),
  groups: (nodeId: string) => apiRequest<{ group: string | null; total: number; live: number; assignable: number }[]>(`${PREFIX}/nodes/${encodeURIComponent(nodeId)}/groups`),
  proxies: (nodeId: string) => apiRequest<OnboardingProxy[]>(`${PREFIX}/nodes/${encodeURIComponent(nodeId)}/proxies`),
  preferences: (selection: OnboardingSelection) => apiRequest<OnboardingSelection>(`${PREFIX}/preferences`, { method: 'PUT', body: JSON.stringify(selection) }),
  addAccount: (body: { email: string; org_id: string; org_name: string }) => apiRequest<OnboardingAccount>(`${PREFIX}/accounts`, { method: 'POST', body: JSON.stringify(body) }),
  addAccounts: (body: { emails: string[]; org_id: string; org_name: string }) => apiRequest<{ accounts: OnboardingAccount[]; invalid: { email: string; reason: string }[] }>(`${PREFIX}/accounts/batch`, { method: 'POST', body: JSON.stringify(body) }),
  deleteAccount: (id: string) => apiRequest(`${PREFIX}/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  createSession: (accountId: string, selection: OnboardingSelection) => apiRequest<{ session: OnboardingSession; profile_id: string }>(`${PREFIX}/sessions`, { method: 'POST', body: JSON.stringify({ account_id: accountId, selection }) }),
  extract: (id: string, optional = false) => apiRequest<{ message?: string; found?: boolean; session: OnboardingSession }>(`${PREFIX}/sessions/${id}/extract${optional ? '?optional=1' : ''}`, { method: 'POST' }),
  submit: (id: string) => apiRequest<{ message: string; session: OnboardingSession; result: unknown }>(`${PREFIX}/sessions/${id}/submit`, { method: 'POST' }),
  result: (id: string) => apiRequest<{ session: OnboardingSession; result: unknown }>(`${PREFIX}/sessions/${id}/result`),
  close: (id: string) => apiRequest<{ session: OnboardingSession }>(`${PREFIX}/sessions/${id}/close`, { method: 'POST' }),
}
