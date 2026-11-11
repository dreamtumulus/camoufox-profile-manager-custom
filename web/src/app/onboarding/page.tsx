'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ClipboardPaste, Copy, KeyRound, LoaderCircle, Plus, RefreshCw, Settings, Square, Upload, Users } from 'lucide-react'
import { AdminPanel } from '@/components/onboarding/admin-panel'
import { OperatorPanel } from '@/components/onboarding/operator-panel'
import { useI18n } from '@/components/i18n-provider'
import { useToast } from '@/components/toast'
import {
  onboardingAPI,
  type OnboardingConfig,
  type OnboardingProxy,
  type OnboardingSelection,
  type OnboardingSession,
  type OneDayOptions,
  type ApiKeyOptions,
  type SharedState,
} from '@/lib/onboarding-api'
import { accountOnboardingState, apiKeyInventory, parseEmailList, proxyAtCapacity, shouldCloseBeforeNext, type AccountOnboardingState } from '@/lib/onboarding-flow'

export default function OnboardingPage() {
  const { t } = useI18n()
  const toast = useToast()
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [showConfig, setShowConfig] = useState(false)
  // Who this installation is to the shared server: a signed-in operator sees
  // only their assignment, the server token holder sees the full console.
  const [remote, setRemote] = useState<OnboardingConfig | null>(null)
  const [adminSetup, setAdminSetup] = useState(false)
  const [showAssignments, setShowAssignments] = useState(false)
  // The operator sign-in form, asked for from the administrator console.
  const [operatorLogin, setOperatorLogin] = useState(false)
  const [serverUrl, setServerUrl] = useState('http://127.0.0.1:9878')
  const [serverToken, setServerToken] = useState('')
  const [credentialToken, setCredentialToken] = useState('')
  const [apiKeyToken, setApiKeyToken] = useState('')
  const [platform, setPlatform] = useState<'1dayapi' | 'api-key.xyz'>('1dayapi')
  const [apiKeyOptions, setApiKeyOptions] = useState<ApiKeyOptions | null>(null)
  const [options, setOptions] = useState<OneDayOptions | null>(null)
  const [shared, setShared] = useState<SharedState | null>(null)
  const [groups, setGroups] = useState<{ group: string | null; total: number; live: number; assignable: number }[]>([])
  const [proxies, setProxies] = useState<OnboardingProxy[]>([])
  const [nodeId, setNodeId] = useState('')
  const [orgId, setOrgId] = useState('')
  const [group, setGroup] = useState('')
  const [proxyId, setProxyId] = useState('')
  const [accountId, setAccountId] = useState('')
  const [session, setSession] = useState<OnboardingSession | null>(null)
  const [email, setEmail] = useState('')
  const [accountOrgId, setAccountOrgId] = useState('')
  const [workflowStep, setWorkflowStep] = useState<1 | 2>(1)
  const [addingAccount, setAddingAccount] = useState(false)
  const [accountStateFilter, setAccountStateFilter] = useState<'all' | AccountOnboardingState>('not_started')
  const [loading, setLoading] = useState(true)
  const [nodeLoading, setNodeLoading] = useState(false)
  const [capacitySyncedAt, setCapacitySyncedAt] = useState<Date | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const platformRef = useRef(platform)
  const apiKeyOptionsRef = useRef(apiKeyOptions)
  useEffect(() => {
    platformRef.current = platform
    apiKeyOptionsRef.current = apiKeyOptions
  }, [platform, apiKeyOptions])

  const activeOptions: OneDayOptions | null = platform === 'api-key.xyz' ? {
    label: 'api-key.xyz', anyNode: false,
    node: { nodeId: 'api-key.xyz', name: 'api-key.xyz', online: true, capacity: 0, used: 0, freeProxies: 0 },
    nodes: [{ nodeId: 'api-key.xyz', name: 'api-key.xyz', online: true, capacity: 0, used: 0, freeProxies: apiKeyOptions?.groups.reduce((sum, item) => sum + item.free_count, 0) ?? 0 }],
    orgs: apiKeyOptions ? [{ id: apiKeyOptions.me.org_id, name: apiKeyOptions.me.org_name, note: null, status: 'active', domains: [], accounts: shared?.accounts.filter(account => account.org_id === apiKeyOptions.me.org_id).length ?? 0 }] : [],
  } : options
  const selectedNode = activeOptions?.nodes.find(node => node.nodeId === nodeId)
  const selectedOrg = activeOptions?.orgs.find(org => org.id === orgId)
  const selectedAccountOrg = activeOptions?.orgs.find(org => org.id === accountOrgId)
  const selectedProxy = proxies.find(proxy => proxy.id === proxyId)
  const selectedAccount = shared?.accounts.find(account => account.id === accountId)
  const shownProxies = useMemo(() => proxies.filter(proxy => !group || proxy.group === group), [proxies, group])
  const shownAccounts = useMemo(() => shared?.accounts.filter(account => account.org_id === orgId) ?? [], [shared, orgId])
  const accountStateLabels: Record<AccountOnboardingState, string> = {
    not_started: '未上号', in_progress: '进行中', ready_to_submit: '待提交 SK', completed: '已上号', failed: '失败（可手动重试）', closed_incomplete: '已关闭未完成',
  }
  function accountState(accountId: string): AccountOnboardingState {
    const latest = shared?.sessions.find(item => item.account_id === accountId && (item.selection.platform ?? '1dayapi') === platform)
    return accountOnboardingState(latest)
  }
  const accountCounts = shownAccounts.reduce<Record<string, number>>((counts, account) => {
    const state = accountState(account.id)
    counts[state] = (counts[state] ?? 0) + 1
    return counts
  }, {})
  const filteredAccounts = shownAccounts.filter(account => accountStateFilter === 'all' || accountState(account.id) === accountStateFilter)
  const selectableAccounts = shownAccounts.filter(account => accountState(account.id) !== 'completed' || account.id === accountId)
  const selectedAccountCompleted = selectedAccount ? accountState(selectedAccount.id) === 'completed' : false

  const loadNode = useCallback(async (id: string, preferredGroup = '', preferredProxy = '', advanceFrom = '') => {
    setNodeLoading(true)
    setError(null)
    try {
      let nextGroups: { group: string | null; total: number; live: number; assignable: number }[] = []
      let nextProxies: OnboardingProxy[] = []
      if (platformRef.current === 'api-key.xyz') {
        if (apiKeyOptionsRef.current) {
          const inventory = apiKeyInventory(apiKeyOptionsRef.current)
          nextGroups = inventory.groups
          nextProxies = inventory.proxies
        }
      } else {
        [nextGroups, nextProxies] = await Promise.all([onboardingAPI.groups(id), onboardingAPI.proxies(id)])
      }
      setGroups(nextGroups)
      setProxies(nextProxies)
      const nextGroup = nextGroups.some(item => (item.group ?? '') === preferredGroup) ? preferredGroup : (nextGroups[0]?.group ?? '')
      setGroup(nextGroup)
      const candidates = nextProxies.filter(proxy => !nextGroup || proxy.group === nextGroup)
      const available = candidates.filter(proxy => !proxyAtCapacity(proxy) && !proxy.quarantined)
      const nextProxy = candidates.some(proxy => proxy.id === preferredProxy)
        ? preferredProxy
        : advanceFrom
          ? (available.find(proxy => proxy.id !== advanceFrom)?.id ?? available[0]?.id ?? candidates[0]?.id ?? '')
          : (available[0]?.id ?? candidates[0]?.id ?? '')
      if (advanceFrom && nextProxy === advanceFrom) setNotice('当前分组没有其他可用 IP，仍选中上一个 IP；请更换分组或等待平台释放容量。')
      setProxyId(nextProxy)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      setGroups([])
      setProxies([])
      setProxyId('')
    } finally { setNodeLoading(false) }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const config = await onboardingAPI.config()
      setRemote(config)
      setConfigured(config.configured)
      if (config.server_url) setServerUrl(config.server_url)
      if (config.mode === 'operator') return
      if (!config.configured) { setShowConfig(true); return }
      const data = await onboardingAPI.bootstrap()
      setOptions(data.oneDay)
      setApiKeyOptions(data.apiKey)
      setShared(data.shared)
      if (data.apiKeyError) setError(`api-key.xyz 暂不可用：${data.apiKeyError}。1dayApi 仍可使用。`)
      const preferences = data.shared.preferences
      const nextPlatform = preferences.platform === 'api-key.xyz' && data.apiKey ? 'api-key.xyz' : '1dayapi'
      setPlatform(nextPlatform)
      platformRef.current = nextPlatform
      apiKeyOptionsRef.current = data.apiKey
      const nextNode = nextPlatform === 'api-key.xyz' ? 'api-key.xyz' : data.oneDay.nodes.some(node => node.nodeId === preferences.node_id)
        ? preferences.node_id! : (data.oneDay.nodes[0]?.nodeId ?? '')
      const nextOrg = nextPlatform === 'api-key.xyz' ? data.apiKey!.me.org_id : data.oneDay.orgs.some(org => org.id === preferences.org_id)
        ? preferences.org_id! : (data.oneDay.orgs[0]?.id ?? '')
      setNodeId(nextNode)
      setOrgId(nextOrg)
      const accounts = data.shared.accounts.filter(account => account.org_id === nextOrg)
      setAccountId(accounts[0]?.id ?? '')
      setSession(data.shared.sessions[0] ?? null)
      if (nextNode) await loadNode(nextNode, preferences.proxy_group ?? '', preferences.proxy_id ?? '')
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally { setLoading(false) }
  }, [loadNode])

  // The initial server read completes asynchronously.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!session || session.status !== 'browser_open') return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const check = async () => {
      try {
        const answer = await onboardingAPI.extract(session.id, true)
        if (cancelled) return
        if (answer.session.status === 'extracted') {
          setSession(answer.session)
          setShared(current => current ? { ...current, sessions: current.sessions.map(item => item.id === answer.session.id ? answer.session : item) } : current)
          setNotice('提取完SK')
          toast('ok', '提取完SK', answer.session.sk_masked ?? '已加密保存到共享服务器')
        } else {
          timer = setTimeout(check, 5000)
        }
      } catch (err) {
        if (!cancelled) setError(`自动提取已停止：${err instanceof Error ? err.message : String(err)}`)
      }
    }
    timer = setTimeout(check, 3000)
    return () => { cancelled = true; if (timer) clearTimeout(timer) }
  }, [session, toast])

  function selection(): OnboardingSelection {
    const defaults = apiKeyOptions?.defaults
    return {
      platform,
      node_id: nodeId,
      node_name: selectedNode?.name ?? selectedNode?.nodeId ?? '',
      org_id: orgId,
      org_name: selectedOrg?.name ?? '',
      proxy_group: group || null,
      proxy_id: proxyId,
      exit_ip: selectedProxy?.exitIp ?? '',
      ...(platform === 'api-key.xyz' && defaults ? {
        seats: defaults.seats,
        rpm: defaults.rpm,
        pool_tags: defaults.pool_tags,
        required_traits: defaults.required_traits,
        forbidden_traits: defaults.forbidden_traits,
      } : {}),
    }
  }

  async function saveConfiguration(event: React.FormEvent) {
    event.preventDefault()
    setBusy('config')
    setError(null)
    try {
      await onboardingAPI.saveConfig({
        server_url: serverUrl.trim(),
        server_token: serverToken.trim(),
        ...(credentialToken.trim() ? { credential_token: credentialToken.trim() } : {}),
        ...(apiKeyToken.trim() ? { api_key_token: apiKeyToken.trim() } : {}),
      })
      setServerToken('')
      setCredentialToken('')
      setApiKeyToken('')
      setShowConfig(false)
      await load()
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function pasteEmail() {
    setBusy('paste')
    setError(null)
    try {
      const value = (await navigator.clipboard.readText()).trim()
      if (!value) throw new Error('剪贴板里没有邮箱地址')
      setEmail(value)
    } catch (err) {
      setError(`无法读取剪贴板，请使用 Ctrl+V 手动粘贴：${err instanceof Error ? err.message : String(err)}`)
    } finally { setBusy('') }
  }

  async function addAccount(event: React.FormEvent) {
    event.preventDefault()
    if (!selectedAccountOrg) { setError('请为新邮箱选择所属组织'); return }
    const emails = parseEmailList(email)
    if (!emails.length) { setError('请至少粘贴一个邮箱地址'); return }
    if (emails.length > 500) { setError('每次最多保存 500 个邮箱'); return }
    setBusy('account')
    setError(null)
    setNotice(null)
    try {
      const result = await onboardingAPI.addAccounts({ emails, org_id: selectedAccountOrg.id, org_name: selectedAccountOrg.name })
      const accounts = result.accounts
      setShared(current => {
        if (!current) return current
        const savedIds = new Set(accounts.map(account => account.id))
        return { ...current, accounts: [...current.accounts.filter(item => !savedIds.has(item.id)), ...accounts].sort((a, b) => a.email.localeCompare(b.email)) }
      })
      if (accounts.length) {
        setOrgId(accounts[0].org_id)
        setAccountId(accounts[0].id)
        setNotice(`已将 ${accounts.length} 个邮箱保存到共享服务器的「${accounts[0].org_name}」邮箱库，可在下方列表查看`)
      }
      if (result.invalid.length) {
        setError(`有 ${result.invalid.length} 项未保存：${result.invalid.map(item => `${item.email}（${item.reason}）`).join('；')}`)
      } else {
        setEmail('')
        setAddingAccount(false)
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  function changePlatform(value: '1dayapi' | 'api-key.xyz') {
    setPlatform(value)
    platformRef.current = value
    setWorkflowStep(1)
    setAccountId('')
    setSession(null)
    setError(null)
    setNotice(null)
    if (value === 'api-key.xyz') {
      setNodeId('api-key.xyz')
      setOrgId(apiKeyOptions?.me.org_id ?? '')
      void loadNode('api-key.xyz')
    } else if (options) {
      const nextNode = options.nodes[0]?.nodeId ?? ''
      setNodeId(nextNode)
      setOrgId(options.orgs[0]?.id ?? '')
      if (nextNode) void loadNode(nextNode)
    }
  }

  async function createWindow() {
    if (!nodeId || !orgId || !proxyId || !selectedProxy) { setError('请完整选择出口节点、归属组织和出口 IP'); return }
    if (proxyAtCapacity(selectedProxy)) { setError(`平台出口 ${selectedProxy.exitIp} 已绑定或预留 ${selectedProxy.capacity ?? 3} 个账号，已达上限，请选择其他 IP`); return }
    if (!selectedAccount) { setError('请先新增并选择邮箱账号'); return }
    setBusy('create')
    setError(null)
    setNotice(null)
    try {
      const currentSelection = selection()
      await onboardingAPI.preferences(currentSelection)
      const answer = await onboardingAPI.createSession(accountId, currentSelection)
      setSession(answer.session)
      setShared(current => current ? { ...current, sessions: [answer.session, ...current.sessions], preferences: currentSelection } : current)
      setNotice(`已为 ${selectedAccount.email} 创建本地 Camoufox 窗口，代理与所选出口一致。登录页会预填此邮箱，请手动完成验证。`)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      try { setShared(await (await onboardingAPI.bootstrap()).shared) } catch { /* 保留原始错误，不自动重试 */ }
    } finally { setBusy('') }
  }

  async function extractNow() {
    if (!session) return
    setBusy('extract')
    setError(null)
    try {
      const answer = await onboardingAPI.extract(session.id)
      setSession(answer.session)
      setNotice('提取完SK')
      toast('ok', '提取完SK')
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function refreshCapacity(advanceFrom = '') {
    if (platform === 'api-key.xyz') {
      const latest = await onboardingAPI.apiKeyOptions()
      setApiKeyOptions(latest)
      apiKeyOptionsRef.current = latest
    }
    await loadNode(nodeId, group, advanceFrom ? '' : proxyId, advanceFrom)
    setCapacitySyncedAt(new Date())
  }

  async function retryWindow() {
    if (!session) return
    setBusy('retry')
    setError(null)
    try {
      const answer = await onboardingAPI.retryBrowser(session.id)
      setSession(answer.session)
      setShared(current => current ? { ...current, sessions: current.sessions.map(item => item.id === answer.session.id ? answer.session : item) } : current)
      setNotice(answer.message)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function copyEmail(value: string) {
    try {
      await navigator.clipboard.writeText(value)
      toast('ok', '邮箱已复制', value)
    } catch (err) {
      setError(`复制失败，请手动选择邮箱复制：${err instanceof Error ? err.message : String(err)}`)
    }
  }

  async function submitNow() {
    if (!session) return
    setBusy('submit')
    setError(null)
    setNotice(null)
    try {
      const answer = await onboardingAPI.submit(session.id)
      setSession(answer.session)
      setShared(current => current ? { ...current, sessions: current.sessions.map(item => item.id === answer.session.id ? answer.session : item) } : current)
      try {
        await refreshCapacity()
        setNotice('SK 已提交，代理池和 IP 容量已同步，请核对下方返回结果')
      } catch (refreshError) {
        setNotice('SK 已提交成功，请核对下方返回结果')
        setError(`账号已成功提交，但代理池同步失败：${refreshError instanceof Error ? refreshError.message : String(refreshError)}。请点击刷新，不要重复提交 SK。`)
      }
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function refreshResult() {
    if (!session) return
    setBusy('result')
    setError(null)
    try {
      const answer = await onboardingAPI.result(session.id)
      setSession(answer.session)
      setShared(current => current ? { ...current, sessions: current.sessions.map(item => item.id === answer.session.id ? answer.session : item) } : current)
      if (answer.session.status === 'completed') await refreshCapacity()
      setNotice(answer.session.status === 'completed' ? '上号已完成，代理池和 IP 容量已同步' : '返回结果已更新')
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function closeWindow() {
    if (!session) return
    setBusy('close')
    setError(null)
    try {
      const answer = await onboardingAPI.close(session.id)
      setSession(answer.session)
      setNotice('浏览器窗口已关闭')
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function finishAndNext() {
    if (!session) return
    setBusy('next')
    setError(null)
    try {
      const finished = shouldCloseBeforeNext(session) ? (await onboardingAPI.close(session.id)).session : session
      setShared(current => current ? { ...current, sessions: current.sessions.map(item => item.id === finished.id ? finished : item) } : current)
      setSession(null)
      setAccountId('')
      setAccountStateFilter('not_started')
      setEmail('')
      setAccountOrgId(orgId)
      setWorkflowStep(2)
      setAddingAccount(true)
      setNotice('上一个账号已保存到最近记录。请粘贴下一个邮箱，或选择已有邮箱。')
      await refreshCapacity(proxyId)
    } catch (err) {
      setError(`无法结束当前账号：${err instanceof Error ? err.message : String(err)}。窗口和记录保持不变，请检查后手动处理。`)
    } finally { setBusy('') }
  }

  if (configured === null || loading) return <div className="p-6 text-ink-dim" role="status"><LoaderCircle className="inline animate-spin" size={15} /> 正在读取上号配置…</div>

  async function forgetAdminToken() {
    if (!window.confirm('移除后，这台电脑不能再进入管理员界面（上号员与分配、共享与接口配置），只能用上号员账号登录。以后需要时可重新填写管理员令牌。确定移除吗？')) return
    setBusy('forget-admin')
    setError(null)
    try {
      const next = await onboardingAPI.forgetAdminToken()
      setRemote(next)
      setConfigured(next.configured)
      setShowConfig(false)
      setShowAssignments(false)
      setAdminSetup(false)
      setOperatorLogin(false)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  const operatorView = remote !== null && (remote.mode === 'operator' || operatorLogin || (!remote.configured && !adminSetup))
  if (operatorView) {
    const adminAvailable = !!remote.admin_token_stored
    return <div className="mx-auto max-w-[1180px] p-5">
      <header className="mb-5 flex flex-wrap items-center gap-3 border-b border-line pb-4">
        <div><h1 className="text-[18px] font-semibold">赵露思 上号系统</h1><p className="mt-1 text-ink-dim">{'登录后自动下发分配给你的邮箱和出口 IP'}</p></div>
        {remote.mode !== 'operator' && adminAvailable && <button className="btn btn-default ml-auto" onClick={() => { setOperatorLogin(false); void load() }}><Settings size={14} />返回管理员界面</button>}
        {remote.mode !== 'operator' && !adminAvailable && <button className="btn btn-default ml-auto" onClick={() => { setOperatorLogin(false); setAdminSetup(true); setShowConfig(true) }}><Settings size={14} />管理员配置</button>}
      </header>
      {error && <div className="mb-4 whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-3 text-danger" role="alert">{error}</div>}
      {adminAvailable && <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 p-3" role="status">
        <span>这台电脑还保存着管理员令牌：未登录或退出登录后，任何人都能回到管理员界面看到全部邮箱和记录。上号员专用的电脑请移除。</span>
        <button className="btn btn-default ml-auto" disabled={busy !== ''} onClick={() => void forgetAdminToken()}>移除本机管理员令牌</button>
      </div>}
      <OperatorPanel config={remote} onConfigChange={next => { setRemote(next); setConfigured(next.configured); if (next.mode !== 'operator' && next.admin_token_stored) setOperatorLogin(true) }} />
    </div>
  }

  return <div className="mx-auto max-w-[1180px] p-5">
    <header className="mb-5 flex flex-wrap items-center gap-3 border-b border-line pb-4">
      <div><h1 className="text-[18px] font-semibold">赵露思 上号系统</h1><p className="mt-1 text-ink-dim">{t('1dayApi.com · Local browsers · Shared onboarding records')}</p></div>
      {configured && <button className={`btn ml-auto ${showAssignments ? 'btn-primary' : 'btn-default'}`} onClick={() => setShowAssignments(current => !current)}><Users size={14} />{showAssignments ? '返回上号' : '上号员与分配'}</button>}
      <button className={`btn btn-default ${configured ? '' : 'ml-auto'}`} onClick={() => setShowConfig(current => !current)}><Settings size={14} />共享与接口配置</button>
      <button className="btn btn-default" onClick={() => { setAdminSetup(false); setShowConfig(false); setError(null); setNotice(null); setOperatorLogin(true) }}>上号员登录</button>
      <button className="btn btn-default" disabled={busy !== ''} onClick={() => void load()}><RefreshCw size={14} />刷新</button>
    </header>

    {error && <div className="mb-4 whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-3 text-danger" role="alert">{error}</div>}
    {notice && <div className="mb-4 rounded-lg border border-ok/40 bg-ok/10 p-3 text-ok" role="status">{notice}</div>}

    {(showConfig || !configured) && <form className="mb-5 rounded-lg border border-line bg-surface p-4" onSubmit={saveConfiguration}>
      <h2 className="font-medium">{t('Shared server and 1dayApi')}</h2>
      <p className="mt-1 text-ink-dim">{t('Selections, email organizations, SK and submission results are stored on the shared server. Credentials are not stored in browser storage.')}</p>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <label className="text-[12px]">共享服务器地址<input className="field mt-1 w-full" value={serverUrl} onChange={event => setServerUrl(event.target.value)} placeholder="https://onboarding.example.com" required /></label>
        <label className="text-[12px]">共享服务器访问令牌<input className="field mt-1 w-full" type="password" value={serverToken} onChange={event => setServerToken(event.target.value)} autoComplete="off" required /></label>
        <label className="text-[12px] md:col-span-2">{t('1dayApi credential')}{shared?.credential_configured ? '（已配置，留空则不更换）' : ''}<input className="field mt-1 w-full" type="password" value={credentialToken} onChange={event => setCredentialToken(event.target.value)} autoComplete="off" placeholder="1dk_onb_…" required={!shared?.credential_configured} /></label>
        <label className="text-[12px] md:col-span-2">{t('api-key.xyz Bearer token')}{shared?.api_key_configured ? '（已配置，留空则不更换）' : ''}<input className="field mt-1 w-full" type="password" value={apiKeyToken} onChange={event => setApiKeyToken(event.target.value)} autoComplete="off" placeholder="用于读取 IP 组和提交 Session Key" /></label>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={busy === 'config'}>{busy === 'config' ? '正在保存…' : '保存并连接'}</button>
        {remote?.admin_token_stored && <button type="button" className="btn btn-default" disabled={busy !== ''} onClick={() => void forgetAdminToken()}>移除本机管理员令牌（改为上号员专用）</button>}
      </div>
    </form>}

    {configured && showAssignments && options && <AdminPanel nodes={options.nodes} orgs={options.orgs} />}

    {configured && !showAssignments && activeOptions && <>
      <section className="mb-4 rounded-lg border border-line bg-surface p-4"><label className="text-[12px]">上号平台<select className="field mt-1 max-w-[340px]" value={platform} disabled={busy !== ''} onChange={event => changePlatform(event.target.value as '1dayapi' | 'api-key.xyz')}><option value="1dayapi">{t('1dayApi.com')}</option><option value="api-key.xyz" disabled={!shared?.api_key_configured}>{t('api-key.xyz')}{shared?.api_key_configured ? '' : '（请先在配置中保存 Bearer 密钥）'}</option></select></label><p className="mt-2 text-ink-dim">{platform === 'api-key.xyz' ? '使用平台默认 Seats / RPM 与池标签；选择组内指定 IP，提交参数由 api-key.xyz 接口返回。' : '使用 1dayApi 的节点、组织、出口分组和 IP。'}</p></section>
      {workflowStep === 1 ? <section className="rounded-lg border border-line bg-surface p-4">
        <h2 className="font-medium">1. 选择出口节点与组织</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-[12px]">出口节点<select className="field mt-1 w-full" value={nodeId} disabled={nodeLoading || busy !== ''} onChange={event => { const id = event.target.value; setNodeId(id); void loadNode(id) }}>
            {activeOptions.nodes.map(node => <option key={node.nodeId} value={node.nodeId}>{node.name ?? node.nodeId}{node.online ? '' : '（离线）'} · 空闲 {node.freeProxies}</option>)}
          </select></label>
          <label className="text-[12px]">归属组织<select className="field mt-1 w-full" value={orgId} disabled={busy !== ''} onChange={event => { setOrgId(event.target.value); setAccountId('') }}>
            {activeOptions.orgs.map(org => <option key={org.id} value={org.id}>{org.name} · {org.accounts} 账号</option>)}
          </select></label>
        </div>
        {nodeLoading && <p className="mt-3 text-ink-dim">{t('Loading node groups and exit IPs…')}</p>}
        <button className="btn btn-primary mt-4" disabled={nodeLoading || busy !== ''} onClick={() => { if (nodeId && orgId) { setError(null); setWorkflowStep(2) } else setError('请先选择出口节点和归属组织') }}>{'下一步：选择分组、IP 和邮箱'}</button>
      </section> : <>
        <section className="rounded-lg border border-line bg-surface p-4">
          <div className="flex items-center gap-3"><h2 className="font-medium">{'2. 选择出口分组与 IP'}</h2><button className="btn btn-default ml-auto" disabled={busy !== ''} onClick={() => setWorkflowStep(1)}>返回修改节点 / 组织</button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            <label className="text-[12px]">出口分组<select className="field mt-1 w-full" value={group} disabled={nodeLoading || busy !== ''} onChange={event => { const value = event.target.value; setGroup(value); const candidates = proxies.filter(proxy => !value || proxy.group === value); setProxyId(candidates[0]?.id ?? '') }}>
              {groups.length === 0 && <option value="">全部代理</option>}
              {groups.map(item => <option key={item.group ?? '__all__'} value={item.group ?? ''}>{item.group ?? '全部代理'} · 可分配 {item.assignable}</option>)}
            </select></label>
            <label className="text-[12px]">{t('Exit IP')}<select id="onboarding-exit-ip" className="field mt-1 w-full" value={proxyId} disabled={nodeLoading || busy !== ''} onChange={event => setProxyId(event.target.value)}>
              {shownProxies.length === 0 && <option value="">{t('No available IPs in this group')}</option>}
              {shownProxies.map(proxy => <option key={proxy.id} value={proxy.id}>{proxy.exitIp} · {proxy.country ?? '—'} · {proxy.host}:{proxy.port} · 已绑 {proxy.boundAccounts}/{proxy.capacity ?? 3}{proxy.reservedAccounts ? ` · 预留 ${proxy.reservedAccounts}` : ''}</option>)}
            </select></label>
          </div>
          {capacitySyncedAt && <p className="mt-3 text-[11px] text-ink-faint">代理池最近同步：{capacitySyncedAt.toLocaleTimeString()} · 每完成一个账号会自动同步一次</p>}
          {selectedProxy && <div className="mt-3 space-y-1 text-ink-dim"><p>{platform === 'api-key.xyz' ? `平台容量：每个 IP 最多 ${selectedProxy.capacity ?? 0} 个账号；当前已绑` : '平台限制：每个 IP 常规绑定 2 个账号，最多 3 个；当前已绑'} <span className="font-mono text-ink">{selectedProxy.boundAccounts}</span>{'，预留'} <span className="font-mono text-ink">{selectedProxy.reservedAccounts}</span>{'，常规剩余'} {selectedProxy.normalSlots}{'，最大容量剩余'} {selectedProxy.overflowSlots}。</p><p>{'创建窗口前将检查本地代理库；没有与'} <span className="font-mono text-ink">{selectedProxy.exitIp}</span> {'一致的唯一代理时会停止。'}</p></div>}
        </section>

        <section className="mt-4 rounded-lg border border-line bg-surface p-4">
          <div className="flex items-center gap-3"><h2 className="font-medium">3. 选择邮箱并创建窗口</h2><button type="button" className="btn btn-default ml-auto" onClick={() => { setAccountOrgId(orgId); setAddingAccount(current => !current) }}><Plus size={13} />新增邮箱</button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
            <select className="field w-full" aria-label="本次上号邮箱" value={accountId} disabled={busy !== ''} onChange={event => setAccountId(event.target.value)}>
              {selectableAccounts.length === 0 && <option value="">没有可继续上号的邮箱，请先新增</option>}
              {selectableAccounts.map(account => <option key={account.id} value={account.id} disabled={accountState(account.id) === 'completed'}>{account.email} · {account.org_name} · {accountStateLabels[accountState(account.id)]}</option>)}
            </select>
            <button className="btn btn-primary" disabled={busy !== '' || nodeLoading || !selectedAccount || selectedAccountCompleted} onClick={() => void createWindow()}>{busy === 'create' ? '正在创建窗口…' : selectedAccountCompleted ? '该邮箱已上号，不可重复创建' : selectedAccount ? `使用 ${selectedAccount.email} 创建窗口` : '请先选择邮箱'}</button>
          </div>
          {selectedAccount && <p className="mt-2 text-ink-dim">本次窗口账号：<span className="font-mono text-ink">{selectedAccount.email}</span> · 所属组织：{selectedAccount.org_name} · 当前平台状态：<span className="font-medium text-ink">{accountStateLabels[accountState(selectedAccount.id)]}</span></p>}
          {addingAccount && <form className="mt-4 space-y-3 border-t border-line pt-4" onSubmit={addAccount}>
            <p className="text-ink-dim">一次粘贴多个邮箱，每行一个，也支持逗号、分号或空格分隔。保存后进入下方共享邮箱库，不会自动创建窗口。</p>
            <div className="flex items-start gap-2"><textarea className="field min-w-[260px] flex-1 resize-y font-mono" rows={4} value={email} autoComplete="off" spellCheck={false} onChange={event => setEmail(event.target.value)} placeholder={'name1@example.com\nname2@example.com'} aria-label="批量邮箱列表" required /><button type="button" className="btn btn-default" disabled={busy !== ''} onClick={() => void pasteEmail()}><ClipboardPaste size={13} />{busy === 'paste' ? '正在读取…' : '粘贴邮箱列表'}</button></div>
            <div className="flex flex-wrap items-end gap-2"><label className="min-w-[260px] flex-1 text-[12px]">这批邮箱所属组织<select className="field mt-1 w-full" value={accountOrgId} disabled={busy !== ''} onChange={event => setAccountOrgId(event.target.value)} required>{activeOptions.orgs.map(org => <option key={org.id} value={org.id}>{org.name}</option>)}</select></label><button className="btn btn-default" disabled={busy !== ''}>{busy === 'account' ? '正在保存…' : `保存 ${parseEmailList(email).length} 个邮箱到共享服务器`}</button></div>
          </form>}
          <div className="mt-4 border-t border-line pt-3">
            <div className="flex flex-wrap items-center gap-3"><h3 className="font-medium">共享服务器邮箱库 · {selectedOrg?.name ?? '当前组织'} · {shownAccounts.length} 个</h3><select className="field field-compact ml-auto w-[150px]" aria-label="按上号状态筛选" value={accountStateFilter} onChange={event => setAccountStateFilter(event.target.value as 'all' | AccountOnboardingState)}><option value="all">全部状态</option>{Object.entries(accountStateLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div>
            <div className="mt-2 flex flex-wrap gap-3 text-[12px]"><span>未上号 <strong>{accountCounts.not_started ?? 0}</strong></span><span className="text-ok">已上号 <strong>{accountCounts.completed ?? 0}</strong></span><span>进行中 / 待提交 <strong>{(accountCounts.in_progress ?? 0) + (accountCounts.ready_to_submit ?? 0)}</strong></span><span className="text-danger">失败 <strong>{accountCounts.failed ?? 0}</strong></span><span className="text-ink-dim">已关闭未完成 <strong>{accountCounts.closed_incomplete ?? 0}</strong></span></div>
            {filteredAccounts.length > 0 ? <ul className="mt-2 max-h-[240px] divide-y divide-line overflow-y-auto rounded-md border border-line">{filteredAccounts.map(account => { const state = accountState(account.id); const latest = shared?.sessions.find(item => item.account_id === account.id && (item.selection.platform ?? '1dayapi') === platform); return <li key={account.id} className="flex items-center gap-3 px-3 py-2">{state === 'completed' ? <span className="min-w-0 flex-1 truncate font-mono">{account.email}</span> : <button type="button" aria-pressed={accountId === account.id} className="min-w-0 flex-1 truncate text-left font-mono hover:text-signal" onClick={() => setAccountId(account.id)} title="选择此邮箱">{account.email}</button>}{accountId === account.id && <span className="shrink-0 text-[11px] text-signal">已选择</span>}<button type="button" className="btn btn-ghost h-7 shrink-0 px-2" aria-label={`复制邮箱 ${account.email}`} title="复制邮箱" onClick={() => void copyEmail(account.email)}><Copy size={12} /></button><span className={`shrink-0 font-medium ${state === 'completed' ? 'text-ok' : state === 'failed' ? 'text-danger' : 'text-ink-dim'}`}>{accountStateLabels[state]}</span>{latest && <span className="hidden shrink-0 text-[11px] text-ink-faint md:inline">{new Date(latest.updated_at).toLocaleString()}</span>}</li> })}</ul> : <p className="mt-2 text-ink-dim">{shownAccounts.length ? '当前筛选下没有邮箱；默认只显示未上号，可切换状态查看历史。' : '此组织还没有邮箱。点击“新增邮箱”可一次粘贴整份列表。'}</p>}
          </div>
        </section>
      </>}

      {session && <section className="mt-4 rounded-lg border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-3"><h2 className="font-medium">4. 登录、提取并提交</h2><span className="rounded bg-raised px-2 py-1 text-[11px]">{accountStateLabels[accountOnboardingState(session)]}</span></div>
        <div className="mt-3 grid gap-2 text-[13px] md:grid-cols-2">
          <p className="flex items-center gap-2">邮箱：<span className="break-all font-mono">{session.email}</span><button type="button" className="btn btn-ghost h-6 px-1.5" aria-label={`复制邮箱 ${session.email}`} onClick={() => void copyEmail(session.email)}><Copy size={12} /></button></p><p>出口：<span className="font-mono">{session.selection.exit_ip}</span></p>
          <p>组织：{session.selection.org_name}</p><p>节点 / 分组：{session.selection.node_name} / {session.selection.proxy_group ?? '全部'}</p>
          <p>{t('SK:')}<span className="font-mono">{session.sk_masked ?? '尚未提取'}</span></p><p>作业编号：<span className="font-mono">{session.job_id ?? '—'}</span></p>
        </div>
        {session.status === 'browser_open' && <p className="mt-3 text-ink-dim">{t('Log in manually in the local window. sessionKey will be extracted automatically, but never submitted automatically.')}</p>}
        {session.error && <p className="mt-3 whitespace-pre-wrap text-danger" role="alert">{session.error}</p>}
        <div className="mt-4 flex flex-wrap gap-2">
          {session.status === 'error' && session.profile_id && <button className="btn btn-default" disabled={busy !== ''} onClick={() => void retryWindow()}><RefreshCw size={14} />{busy === 'retry' ? '正在恢复原窗口…' : '重试并复用原窗口'}</button>}
          <button className="btn btn-default" disabled={busy !== ''} onClick={() => void extractNow()}><KeyRound size={14} />{busy === 'extract' ? '正在提取…' : '手动提取 SK'}</button>
          <button className="btn btn-primary" disabled={busy !== '' || !session.sk_masked || !!session.job_id} onClick={() => void submitNow()}><Upload size={14} />{busy === 'submit' ? '正在提交（仅一次）…' : session.status === 'error' && session.sk_masked ? '手动重试提交 SK' : '提交 SK（仅一次）'}</button>
          {session.selection.platform !== 'api-key.xyz' && <button className="btn btn-default" disabled={busy !== '' || !session.job_id} onClick={() => void refreshResult()}><RefreshCw size={14} />{busy === 'result' ? '正在查询…' : '刷新返回结果'}</button>}
          <button className="btn btn-default" disabled={busy !== '' || !session.profile_id} onClick={() => void closeWindow()}><Square size={13} />关闭窗口</button>
          {(session.job_id || session.status === 'submitted') && <button className="btn btn-primary" disabled={busy !== ''} onClick={() => void finishAndNext()}><Plus size={13} />{busy === 'next' ? '正在保存并刷新容量…' : '完成此账号，继续下一个'}</button>}
        </div>
        {session.result !== null && session.result !== undefined && <div className="mt-4"><h3 className="font-medium">{t('1dayApi response')}</h3><pre className="mt-2 max-h-[360px] overflow-auto rounded-lg border border-line bg-canvas p-3 text-[12px] text-ink">{JSON.stringify(session.result, null, 2)}</pre></div>}
      </section>}

      {shared && shared.sessions.length > 0 && <section className="mt-4 rounded-lg border border-line bg-surface p-4"><h2 className="font-medium">共享服务器上的最近记录</h2><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[680px] text-left text-[12px]"><tbody>{shared.sessions.slice(0, 20).map(item => <tr key={item.id} className="border-t border-line/60"><td className="py-2 pr-3"><button className="text-left text-signal hover:underline" onClick={() => { setSession(item); setError(null); setNotice(null) }}>{item.email}</button></td><td className="py-2 pr-3 font-mono">{item.selection.exit_ip}</td><td className="py-2 pr-3">{item.status}</td><td className="py-2 font-mono">{item.sk_masked ?? '—'}</td></tr>)}</tbody></table></div></section>}
    </>}
  </div>
}
