'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { ClipboardPaste, KeyRound, LoaderCircle, Plus, RefreshCw, Settings, Square, Upload } from 'lucide-react'
import { useI18n } from '@/components/i18n-provider'
import { useToast } from '@/components/toast'
import {
  onboardingAPI,
  type OnboardingProxy,
  type OnboardingSelection,
  type OnboardingSession,
  type OneDayOptions,
  type SharedState,
} from '@/lib/onboarding-api'
import { proxyAtCapacity, shouldCloseBeforeNext } from '@/lib/onboarding-flow'

export default function OnboardingPage() {
  const { t } = useI18n()
  const toast = useToast()
  const [configured, setConfigured] = useState<boolean | null>(null)
  const [showConfig, setShowConfig] = useState(false)
  const [serverUrl, setServerUrl] = useState('http://127.0.0.1:9878')
  const [serverToken, setServerToken] = useState('')
  const [credentialToken, setCredentialToken] = useState('')
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
  const [loading, setLoading] = useState(true)
  const [nodeLoading, setNodeLoading] = useState(false)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const selectedNode = options?.nodes.find(node => node.nodeId === nodeId)
  const selectedOrg = options?.orgs.find(org => org.id === orgId)
  const selectedAccountOrg = options?.orgs.find(org => org.id === accountOrgId)
  const selectedProxy = proxies.find(proxy => proxy.id === proxyId)
  const shownProxies = useMemo(() => proxies.filter(proxy => !group || proxy.group === group), [proxies, group])
  const shownAccounts = useMemo(() => shared?.accounts.filter(account => account.org_id === orgId) ?? [], [shared, orgId])

  const loadNode = useCallback(async (id: string, preferredGroup = '', preferredProxy = '') => {
    setNodeLoading(true)
    setError(null)
    try {
      const [nextGroups, nextProxies] = await Promise.all([onboardingAPI.groups(id), onboardingAPI.proxies(id)])
      setGroups(nextGroups)
      setProxies(nextProxies)
      const nextGroup = nextGroups.some(item => (item.group ?? '') === preferredGroup) ? preferredGroup : (nextGroups[0]?.group ?? '')
      setGroup(nextGroup)
      const candidates = nextProxies.filter(proxy => !nextGroup || proxy.group === nextGroup)
      setProxyId(candidates.some(proxy => proxy.id === preferredProxy) ? preferredProxy : (candidates[0]?.id ?? ''))
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
      setConfigured(config.configured)
      if (config.server_url) setServerUrl(config.server_url)
      if (!config.configured) { setShowConfig(true); return }
      const data = await onboardingAPI.bootstrap()
      setOptions(data.oneDay)
      setShared(data.shared)
      const preferences = data.shared.preferences
      const nextNode = data.oneDay.nodes.some(node => node.nodeId === preferences.node_id)
        ? preferences.node_id! : (data.oneDay.nodes[0]?.nodeId ?? '')
      const nextOrg = data.oneDay.orgs.some(org => org.id === preferences.org_id)
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
    return {
      node_id: nodeId,
      node_name: selectedNode?.name ?? selectedNode?.nodeId ?? '',
      org_id: orgId,
      org_name: selectedOrg?.name ?? '',
      proxy_group: group || null,
      proxy_id: proxyId,
      exit_ip: selectedProxy?.exitIp ?? '',
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
      })
      setServerToken('')
      setCredentialToken('')
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
    setBusy('account')
    setError(null)
    try {
      const account = await onboardingAPI.addAccount({ email: email.trim(), org_id: selectedAccountOrg.id, org_name: selectedAccountOrg.name })
      setShared(current => current ? { ...current, accounts: [...current.accounts.filter(item => item.id !== account.id), account] } : current)
      setOrgId(account.org_id)
      setAccountId(account.id)
      setEmail('')
      setAddingAccount(false)
      setNotice(`已保存邮箱 ${account.email}（${account.org_name}）到共享服务器`)
    } catch (err) { setError(err instanceof Error ? err.message : String(err)) }
    finally { setBusy('') }
  }

  async function createWindow() {
    if (!nodeId || !orgId || !proxyId || !selectedProxy) { setError('请完整选择出口节点、归属组织和出口 IP'); return }
    if (proxyAtCapacity(selectedProxy)) { setError(`平台出口 ${selectedProxy.exitIp} 已绑定或预留 3 个账号，已达上限，请选择其他 IP`); return }
    if (!accountId) { setError('请先新增并选择邮箱账号'); return }
    setBusy('create')
    setError(null)
    setNotice(null)
    try {
      const currentSelection = selection()
      await onboardingAPI.preferences(currentSelection)
      const answer = await onboardingAPI.createSession(accountId, currentSelection)
      setSession(answer.session)
      setShared(current => current ? { ...current, sessions: [answer.session, ...current.sessions], preferences: currentSelection } : current)
      setNotice('本地 Camoufox 窗口已创建，代理已与所选出口 IP 匹配。请在窗口内手动登录。')
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

  async function submitNow() {
    if (!session) return
    setBusy('submit')
    setError(null)
    setNotice(null)
    try {
      const answer = await onboardingAPI.submit(session.id)
      setSession(answer.session)
      setNotice('SK 已提交，请核对下方返回结果')
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
      setNotice('返回结果已更新')
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
      setEmail('')
      setAccountOrgId(orgId)
      setWorkflowStep(2)
      setAddingAccount(true)
      setNotice('上一个账号已保存到最近记录。请粘贴下一个邮箱，或选择已有邮箱。')
      await loadNode(nodeId, group, proxyId)
    } catch (err) {
      setError(`无法结束当前账号：${err instanceof Error ? err.message : String(err)}。窗口和记录保持不变，请检查后手动处理。`)
    } finally { setBusy('') }
  }

  if (configured === null || loading) return <div className="p-6 text-ink-dim" role="status"><LoaderCircle className="inline animate-spin" size={15} /> 正在读取上号配置…</div>

  return <div className="mx-auto max-w-[1180px] p-5">
    <header className="mb-5 flex flex-wrap items-center gap-3 border-b border-line pb-4">
      <div><h1 className="text-[18px] font-semibold">赵露思 上号系统</h1><p className="mt-1 text-ink-dim">{t('1dayApi.com · Local browsers · Shared onboarding records')}</p></div>
      <button className="btn btn-default ml-auto" onClick={() => setShowConfig(current => !current)}><Settings size={14} />共享与接口配置</button>
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
      </div>
      <button className="btn btn-primary mt-4" disabled={busy === 'config'}>{busy === 'config' ? '正在保存…' : '保存并连接'}</button>
    </form>}

    {configured && options && <>
      {workflowStep === 1 ? <section className="rounded-lg border border-line bg-surface p-4">
        <h2 className="font-medium">1. 选择出口节点与组织</h2>
        <div className="mt-4 grid gap-3 md:grid-cols-2">
          <label className="text-[12px]">出口节点<select className="field mt-1 w-full" value={nodeId} disabled={nodeLoading || busy !== ''} onChange={event => { const id = event.target.value; setNodeId(id); void loadNode(id) }}>
            {options.nodes.map(node => <option key={node.nodeId} value={node.nodeId}>{node.name ?? node.nodeId}{node.online ? '' : '（离线）'} · 空闲 {node.freeProxies}</option>)}
          </select></label>
          <label className="text-[12px]">归属组织<select className="field mt-1 w-full" value={orgId} disabled={busy !== ''} onChange={event => { setOrgId(event.target.value); setAccountId('') }}>
            {options.orgs.map(org => <option key={org.id} value={org.id}>{org.name} · {org.accounts} 账号</option>)}
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
            <label className="text-[12px]">{t('Exit IP')}<select className="field mt-1 w-full" value={proxyId} disabled={nodeLoading || busy !== ''} onChange={event => setProxyId(event.target.value)}>
              {shownProxies.length === 0 && <option value="">{t('No available IPs in this group')}</option>}
              {shownProxies.map(proxy => <option key={proxy.id} value={proxy.id}>{proxy.exitIp} · {proxy.country ?? '—'} · {proxy.host}:{proxy.port} · 已绑 {proxy.boundAccounts}/3{proxy.reservedAccounts ? ` · 预留 ${proxy.reservedAccounts}` : ''}</option>)}
            </select></label>
          </div>
          {selectedProxy && <div className="mt-3 space-y-1 text-ink-dim"><p>{'平台限制：每个 IP 常规绑定 2 个账号，最多 3 个；当前已绑'} <span className="font-mono text-ink">{selectedProxy.boundAccounts}</span>{'，预留'} <span className="font-mono text-ink">{selectedProxy.reservedAccounts}</span>{'，常规剩余'} {selectedProxy.normalSlots}{'，最大容量剩余'} {selectedProxy.overflowSlots}。</p><p>{'创建窗口前将检查本地代理库；没有与'} <span className="font-mono text-ink">{selectedProxy.exitIp}</span> {'一致的唯一代理时会停止。'}</p></div>}
        </section>

        <section className="mt-4 rounded-lg border border-line bg-surface p-4">
          <div className="flex items-center gap-3"><h2 className="font-medium">3. 选择邮箱并创建窗口</h2><button type="button" className="btn btn-default ml-auto" onClick={() => { setAccountOrgId(orgId); setAddingAccount(current => !current) }}><Plus size={13} />新增邮箱</button></div>
          <div className="mt-4 grid gap-3 md:grid-cols-[1fr_auto]">
            <select className="field w-full" value={accountId} disabled={busy !== ''} onChange={event => setAccountId(event.target.value)}>
              {shownAccounts.length === 0 && <option value="">当前组织没有邮箱，请先新增</option>}
              {shownAccounts.map(account => <option key={account.id} value={account.id}>{account.email} · {account.org_name}</option>)}
            </select>
            <button className="btn btn-primary" disabled={busy !== '' || nodeLoading} onClick={() => void createWindow()}>{busy === 'create' ? '正在创建窗口…' : '保存选择并创建 Camoufox 窗口'}</button>
          </div>
          {addingAccount && <form className="mt-4 space-y-3 border-t border-line pt-4" onSubmit={addAccount}>
            <div className="flex flex-wrap gap-2"><input className="field min-w-[260px] flex-1" type="email" value={email} autoComplete="email" spellCheck={false} onChange={event => setEmail(event.target.value)} placeholder="粘贴或输入 name@example.com" required /><button type="button" className="btn btn-default" disabled={busy !== ''} onClick={() => void pasteEmail()}><ClipboardPaste size={13} />{busy === 'paste' ? '正在读取…' : '粘贴邮箱'}</button></div>
            <div className="flex flex-wrap items-end gap-2"><label className="min-w-[260px] flex-1 text-[12px]">邮箱所属组织<select className="field mt-1 w-full" value={accountOrgId} disabled={busy !== ''} onChange={event => setAccountOrgId(event.target.value)} required>{options.orgs.map(org => <option key={org.id} value={org.id}>{org.name}</option>)}</select></label><button className="btn btn-default" disabled={busy !== ''}>{busy === 'account' ? '正在保存…' : '保存邮箱与组织'}</button></div>
          </form>}
        </section>
      </>}

      {session && <section className="mt-4 rounded-lg border border-line bg-surface p-4">
        <div className="flex flex-wrap items-center gap-3"><h2 className="font-medium">4. 登录、提取并提交</h2><span className="rounded bg-raised px-2 py-1 font-mono text-[11px]">{session.status}</span></div>
        <div className="mt-3 grid gap-2 text-[13px] md:grid-cols-2">
          <p>邮箱：<span className="font-mono">{session.email}</span></p><p>出口：<span className="font-mono">{session.selection.exit_ip}</span></p>
          <p>组织：{session.selection.org_name}</p><p>节点 / 分组：{session.selection.node_name} / {session.selection.proxy_group ?? '全部'}</p>
          <p>{t('SK:')}<span className="font-mono">{session.sk_masked ?? '尚未提取'}</span></p><p>作业编号：<span className="font-mono">{session.job_id ?? '—'}</span></p>
        </div>
        {session.status === 'browser_open' && <p className="mt-3 text-ink-dim">{t('Log in manually in the local window. sessionKey will be extracted automatically, but never submitted automatically.')}</p>}
        {session.error && <p className="mt-3 whitespace-pre-wrap text-danger" role="alert">{session.error}</p>}
        <div className="mt-4 flex flex-wrap gap-2">
          <button className="btn btn-default" disabled={busy !== ''} onClick={() => void extractNow()}><KeyRound size={14} />{busy === 'extract' ? '正在提取…' : '手动提取 SK'}</button>
          <button className="btn btn-primary" disabled={busy !== '' || !session.sk_masked || !!session.job_id} onClick={() => void submitNow()}><Upload size={14} />{busy === 'submit' ? '正在提交（仅一次）…' : '提交 SK（仅一次）'}</button>
          <button className="btn btn-default" disabled={busy !== '' || !session.job_id} onClick={() => void refreshResult()}><RefreshCw size={14} />{busy === 'result' ? '正在查询…' : '刷新返回结果'}</button>
          <button className="btn btn-default" disabled={busy !== '' || !session.profile_id} onClick={() => void closeWindow()}><Square size={13} />关闭窗口</button>
          {session.job_id && <button className="btn btn-primary" disabled={busy !== ''} onClick={() => void finishAndNext()}><Plus size={13} />{busy === 'next' ? '正在保存并刷新容量…' : '完成此账号，继续下一个'}</button>}
        </div>
        {session.result !== null && session.result !== undefined && <div className="mt-4"><h3 className="font-medium">{t('1dayApi response')}</h3><pre className="mt-2 max-h-[360px] overflow-auto rounded-lg border border-line bg-canvas p-3 text-[12px] text-ink">{JSON.stringify(session.result, null, 2)}</pre></div>}
      </section>}

      {shared && shared.sessions.length > 0 && <section className="mt-4 rounded-lg border border-line bg-surface p-4"><h2 className="font-medium">共享服务器上的最近记录</h2><div className="mt-3 overflow-x-auto"><table className="w-full min-w-[680px] text-left text-[12px]"><tbody>{shared.sessions.slice(0, 20).map(item => <tr key={item.id} className="border-t border-line/60"><td className="py-2 pr-3"><button className="text-left text-signal hover:underline" onClick={() => { setSession(item); setError(null); setNotice(null) }}>{item.email}</button></td><td className="py-2 pr-3 font-mono">{item.selection.exit_ip}</td><td className="py-2 pr-3">{item.status}</td><td className="py-2 font-mono">{item.sk_masked ?? '—'}</td></tr>)}</tbody></table></div></section>}
    </>}
  </div>
}
