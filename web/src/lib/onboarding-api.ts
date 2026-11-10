import { apiRequest } from './api'

const PREFIX = '/api/v1/onboarding'

export interface OnboardingSelection {
  platform?: '1dayapi' | 'api-key.xyz'
  node_id: string
  node_name: string
  org_id: string
  org_name: string
  proxy_group: string | null
  proxy_id: string
  exit_ip: string
  seats?: number
  rpm?: number
  pool_tags?: string[]
  required_traits?: string[]
  forbidden_traits?: string[]
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
  capacity?: number
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
  api_key_configured?: boolean
}

export interface ApiKeyOptions {
  me: { org_id: string; org_name: string }
  defaults: {
    seats: number
    rpm: number
    pool_tags: string[]
    required_traits: string[]
    forbidden_traits: string[]
  }
  groups: {
    name: string
    display_name: string
    ip_count: number
    ready_count: number
    free_count: number
    ips: {
      id: string
      server: string
      port: number
      status: string
      accounts: number
      signing_in: number
      capacity: number
      free: number
      observed_ip: string
    }[]
  }[]
}

export interface OneDayOptions {
  label: string
  node: OnboardingNode
  nodes: OnboardingNode[]
  anyNode: boolean
  orgs: OnboardingOrg[]
}

export interface OnboardingOperator {
  id: string
  username: string
  disabled: boolean
  created_at: string
}

export interface OnboardingConfig {
  configured: boolean
  server_url: string | null
  mode?: 'operator' | 'admin' | null
  operator?: OnboardingOperator | null
  message?: string
}

/** A proxy in the shared pool. Passwords never reach the web UI. */
export interface PoolProxy {
  id: string
  platform: '1dayapi' | 'api-key.xyz'
  node_id: string
  node_name: string
  proxy_group: string | null
  platform_proxy_id: string
  exit_ip: string
  type: string
  server: string
  username: string | null
  capacity: number
  used: number
  operator_id: string | null
  has_password: boolean
}

export interface OperatorSession extends OnboardingSession {
  browser_running?: boolean
}

export interface OperatorTask {
  id: string
  email: string
  org_id: string
  org_name: string
  platform: '1dayapi' | 'api-key.xyz' | null
  proxy_id: string | null
  exit_ip: string | null
  session: OperatorSession | null
}

export interface OperatorBundle {
  operator: OnboardingOperator
  proxies: PoolProxy[]
  tasks: OperatorTask[]
  sessions: OperatorSession[]
}

export interface AdminOperator extends OnboardingOperator {
  proxies: number
  accounts: number
  completed: number
}

export interface AdminAccount {
  id: string
  email: string
  org_id: string
  org_name: string
  platform: string | null
  operator_id: string | null
  proxy_id: string | null
  status: string | null
}

export interface AdminOverview {
  operators: AdminOperator[]
  proxies: PoolProxy[]
  accounts: AdminAccount[]
  audit: { id: number; at: string; actor: string; action: string; detail_json: string }[]
}

export interface ProxyImportResult {
  imported: PoolProxy[]
  unmatched: { line: number; server: string }[]
}

export interface AccountImportResult {
  placed: (AdminAccount & { exit_ip?: string })[]
  unplaced: { email: string; reason: string }[]
  invalid: { email: string; reason: string }[]
}

export const onboardingAPI = {
  config: () => apiRequest<OnboardingConfig>(`${PREFIX}/config`),
  saveConfig: (body: { server_url: string; server_token: string; credential_token?: string; api_key_token?: string }) =>
    apiRequest<OnboardingConfig>(`${PREFIX}/config`, { method: 'PUT', body: JSON.stringify(body) }),
  bootstrap: () => apiRequest<{ shared: SharedState; oneDay: OneDayOptions; apiKey: ApiKeyOptions | null; apiKeyError?: string | null }>(`${PREFIX}/bootstrap`),
  /** The same route answers with the personal assignment once an operator has signed in. */
  operatorBundle: async () => (await apiRequest<{ operator: OperatorBundle }>(`${PREFIX}/bootstrap`)).operator,
  operatorLogin: (body: { username: string; password: string; server_url?: string }) =>
    apiRequest<OnboardingConfig>(`${PREFIX}/operator/login`, { method: 'POST', body: JSON.stringify(body) }),
  operatorLogout: () => apiRequest<OnboardingConfig>(`${PREFIX}/operator/logout`, { method: 'POST' }),
  startTask: (accountId: string) =>
    apiRequest<{ session: OperatorSession; profile_id: string }>(`${PREFIX}/operator/tasks/${encodeURIComponent(accountId)}/start`, { method: 'POST' }),
  adminOverview: () => apiRequest<AdminOverview>(`${PREFIX}/admin/overview`),
  adminCreateOperator: (body: { username: string; password: string }) =>
    apiRequest<OnboardingOperator>(`${PREFIX}/admin/operators`, { method: 'POST', body: JSON.stringify(body) }),
  adminUpdateOperator: (id: string, body: { password?: string; disabled?: boolean }) =>
    apiRequest<OnboardingOperator>(`${PREFIX}/admin/operators/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  adminImportProxies: (body: { platform: string; node_id: string; node_name: string; text: string; capacity: number; operator_id: string | null; defaults?: Record<string, unknown> }) =>
    apiRequest<ProxyImportResult>(`${PREFIX}/admin/proxies/import`, { method: 'POST', body: JSON.stringify(body) }),
  adminAssignProxies: (proxyIds: string[], operatorId: string | null) =>
    apiRequest<{ assigned: number }>(`${PREFIX}/admin/proxies/assign`, { method: 'POST', body: JSON.stringify({ proxy_ids: proxyIds, operator_id: operatorId }) }),
  adminDeleteProxy: (id: string) => apiRequest(`${PREFIX}/admin/proxies/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  adminImportAccounts: (body: { operator_id: string; platform: string; org_id: string; org_name: string; emails: string[] }) =>
    apiRequest<AccountImportResult>(`${PREFIX}/admin/accounts/import`, { method: 'POST', body: JSON.stringify(body) }),
  adminUnassignAccount: (id: string) => apiRequest(`${PREFIX}/admin/accounts/${encodeURIComponent(id)}/unassign`, { method: 'POST' }),
  groups: (nodeId: string) => apiRequest<{ group: string | null; total: number; live: number; assignable: number }[]>(`${PREFIX}/nodes/${encodeURIComponent(nodeId)}/groups`),
  proxies: (nodeId: string) => apiRequest<OnboardingProxy[]>(`${PREFIX}/nodes/${encodeURIComponent(nodeId)}/proxies`),
  apiKeyOptions: () => apiRequest<ApiKeyOptions>(`${PREFIX}/api-key/options`),
  preferences: (selection: OnboardingSelection) => apiRequest<OnboardingSelection>(`${PREFIX}/preferences`, { method: 'PUT', body: JSON.stringify(selection) }),
  addAccount: (body: { email: string; org_id: string; org_name: string }) => apiRequest<OnboardingAccount>(`${PREFIX}/accounts`, { method: 'POST', body: JSON.stringify(body) }),
  addAccounts: (body: { emails: string[]; org_id: string; org_name: string }) => apiRequest<{ accounts: OnboardingAccount[]; invalid: { email: string; reason: string }[] }>(`${PREFIX}/accounts/batch`, { method: 'POST', body: JSON.stringify(body) }),
  deleteAccount: (id: string) => apiRequest(`${PREFIX}/accounts/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  createSession: (accountId: string, selection: OnboardingSelection) => apiRequest<{ session: OnboardingSession; profile_id: string }>(`${PREFIX}/sessions`, { method: 'POST', body: JSON.stringify({ account_id: accountId, selection }) }),
  extract: (id: string, optional = false) => apiRequest<{ message?: string; found?: boolean; session: OnboardingSession }>(`${PREFIX}/sessions/${id}/extract${optional ? '?optional=1' : ''}`, { method: 'POST' }),
  submit: (id: string) => apiRequest<{ message: string; session: OnboardingSession; result: unknown }>(`${PREFIX}/sessions/${id}/submit`, { method: 'POST' }),
  result: (id: string) => apiRequest<{ session: OnboardingSession; result: unknown }>(`${PREFIX}/sessions/${id}/result`),
  close: (id: string) => apiRequest<{ session: OnboardingSession }>(`${PREFIX}/sessions/${id}/close`, { method: 'POST' }),
  retryBrowser: (id: string) => apiRequest<{ session: OnboardingSession; message: string }>(`${PREFIX}/sessions/${id}/retry-browser`, { method: 'POST' }),
}
