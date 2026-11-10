'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { LoaderCircle, RefreshCw, Upload, UserPlus } from 'lucide-react'
import { useToast } from '@/components/toast'
import { onboardingAPI, type AccountImportResult, type AdminOverview, type OnboardingNode, type OnboardingOrg, type ProxyImportResult } from '@/lib/onboarding-api'
import { parseEmailList } from '@/lib/onboarding-flow'

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

const ACCOUNT_STATE: Record<string, string> = {
  creating: '进行中', launching: '进行中', browser_open: '进行中', extracted: '待提交 SK', submitting: '提交中',
  submitted: '已提交', completed: '已上号', failed: '失败', error: '失败', closed: '已关闭未完成',
}

/** Administrator view: operators, the exit-IP pool and who gets which e-mails. */
export function AdminPanel({ nodes, orgs }: { nodes: OnboardingNode[]; orgs: OnboardingOrg[] }) {
  const toast = useToast()
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [newName, setNewName] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [nodeId, setNodeId] = useState(nodes[0]?.nodeId ?? '')
  const [proxyText, setProxyText] = useState('')
  const [capacity, setCapacity] = useState(2)
  const [proxyOperator, setProxyOperator] = useState('')
  const [proxyResult, setProxyResult] = useState<ProxyImportResult | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [assignTo, setAssignTo] = useState('')
  const [mailOperator, setMailOperator] = useState('')
  const [mailOrg, setMailOrg] = useState(orgs[0]?.id ?? '')
  const [mailText, setMailText] = useState('')
  const [mailResult, setMailResult] = useState<AccountImportResult | null>(null)

  const refresh = useCallback(async () => {
    try {
      setOverview(await onboardingAPI.adminOverview())
    } catch (err) { setError(message(err)) }
  }, [])
  useEffect(() => {
    const first = setTimeout(() => { void refresh() }, 0)
    return () => clearTimeout(first)
  }, [refresh])

  const operatorName = useMemo(() => new Map(overview?.operators.map(item => [item.id, item.username]) ?? []), [overview])
  const proxyIp = useMemo(() => new Map(overview?.proxies.map(item => [item.id, item.exit_ip]) ?? []), [overview])
  const emails = parseEmailList(mailText)
  const freeSlots = overview?.proxies.filter(proxy => proxy.operator_id === mailOperator).reduce((sum, proxy) => sum + Math.max(0, proxy.capacity - proxy.used), 0) ?? 0

  async function run(key: string, work: () => Promise<void>) {
    setBusy(key)
    setError(null)
    try { await work() } catch (err) { setError(message(err)) }
    finally {
      setBusy('')
      await refresh()
    }
  }

  const createOperator = (event: React.FormEvent) => {
    event.preventDefault()
    void run('operator', async () => {
      await onboardingAPI.adminCreateOperator({ username: newName.trim(), password: newPassword })
      toast('ok', '上号员已创建', newName.trim())
      setNewName('')
      setNewPassword('')
    })
  }

  function resetPassword(id: string, name: string) {
    const password = window.prompt(`为 ${name} 设置新密码（至少 8 位）。保存后该账号现有登录会全部失效。`)
    if (!password) return
    void run(`reset:${id}`, async () => {
      await onboardingAPI.adminUpdateOperator(id, { password })
      toast('ok', '密码已重置', name)
    })
  }

  const importProxies = (event: React.FormEvent) => {
    event.preventDefault()
    const node = nodes.find(item => item.nodeId === nodeId)
    if (!node) { setError('请选择出口节点'); return }
    void run('proxies', async () => {
      const result = await onboardingAPI.adminImportProxies({
        platform: '1dayapi', node_id: node.nodeId, node_name: node.name ?? node.nodeId,
        text: proxyText, capacity, operator_id: proxyOperator || null,
      })
      setProxyResult(result)
      if (result.unmatched.length === 0) setProxyText('')
      toast('ok', `已导入 ${result.imported.length} 个 IP`, result.unmatched.length ? `${result.unmatched.length} 行未匹配` : undefined)
    })
  }

  const importAccounts = (event: React.FormEvent) => {
    event.preventDefault()
    const org = orgs.find(item => item.id === mailOrg)
    if (!org || !mailOperator) { setError('请选择上号员和归属组织'); return }
    void run('accounts', async () => {
      const result = await onboardingAPI.adminImportAccounts({
        operator_id: mailOperator, platform: '1dayapi', org_id: org.id, org_name: org.name, emails,
      })
      setMailResult(result)
      if (result.unplaced.length === 0 && result.invalid.length === 0) setMailText('')
      toast('ok', `已分配 ${result.placed.length} 个邮箱`, result.unplaced.length ? `${result.unplaced.length} 个未能分配` : undefined)
    })
  }

  if (overview === null) {
    return <div className="p-6 text-ink-dim" role="status">
      {error ? <span className="text-danger">{error}</span> : <><LoaderCircle className="inline animate-spin" size={15} /> 正在读取分配数据…</>}
    </div>
  }
  const operatorOptions = overview.operators.filter(item => !item.disabled)

  return <div className="grid gap-4">
    {error && <div className="whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-3 text-danger" role="alert">{error}</div>}

    <section className="rounded-lg border border-line bg-surface p-4">
      <div className="flex items-center gap-3"><h2 className="font-medium">上号员</h2><button className="btn btn-default ml-auto" disabled={busy !== ''} onClick={() => void refresh()}><RefreshCw size={14} />刷新</button></div>
      <table className="mt-3 w-full text-left text-[13px]">
        <thead className="border-b border-line text-ink-dim"><tr><th className="p-2 font-medium">账号</th><th className="p-2 font-medium">{'IP'}</th><th className="p-2 font-medium">邮箱</th><th className="p-2 font-medium">已上号</th><th className="p-2 font-medium">状态</th><th className="p-2 font-medium">操作</th></tr></thead>
        <tbody>
          {overview.operators.length === 0 && <tr><td className="p-2 text-ink-dim" colSpan={6}>还没有上号员，先在下方创建。</td></tr>}
          {overview.operators.map(item => <tr key={item.id} className="border-b border-line last:border-0">
            <td className="p-2">{item.username}</td><td className="p-2">{item.proxies}</td><td className="p-2">{item.accounts}</td><td className="p-2">{item.completed}</td>
            <td className="p-2">{item.disabled ? <span className="text-danger">已停用</span> : <span className="text-ok">正常</span>}</td>
            <td className="p-2"><div className="flex flex-wrap gap-2">
              <button className="btn btn-default" disabled={busy !== ''} onClick={() => resetPassword(item.id, item.username)}>重置密码</button>
              <button className="btn btn-default" disabled={busy !== ''} onClick={() => void run(`toggle:${item.id}`, async () => { await onboardingAPI.adminUpdateOperator(item.id, { disabled: !item.disabled }) })}>{item.disabled ? '启用' : '停用'}</button>
            </div></td>
          </tr>)}
        </tbody>
      </table>
      <form className="mt-4 flex flex-wrap items-end gap-3" onSubmit={createOperator}>
        <label className="text-[12px]">新上号员账号<input className="field mt-1 w-[200px]" value={newName} onChange={event => setNewName(event.target.value)} pattern="[A-Za-z0-9._@-]{2,64}" autoComplete="off" required /></label>
        <label className="text-[12px]">初始密码（至少 8 位）<input className="field mt-1 w-[200px]" type="password" value={newPassword} onChange={event => setNewPassword(event.target.value)} minLength={8} autoComplete="new-password" required /></label>
        <button className="btn btn-primary" disabled={busy !== ''}><UserPlus size={14} />创建</button>
      </form>
    </section>

    <section className="rounded-lg border border-line bg-surface p-4">
      <h2 className="font-medium">{'出口 IP 池'}</h2>
      <p className="mt-1 text-ink-dim">{'先在 1dayApi 导入代理，再把同一批代理粘贴到这里。系统按 host:port 自动对上平台里的 IP；平台里没有的行不会保存。'}</p>
      <form className="mt-3 grid gap-3 md:grid-cols-4" onSubmit={importProxies}>
        <label className="text-[12px]">出口节点<select className="field mt-1 w-full" value={nodeId} onChange={event => setNodeId(event.target.value)}>{nodes.map(node => <option key={node.nodeId} value={node.nodeId}>{node.name ?? node.nodeId}</option>)}</select></label>
        <label className="text-[12px]">{'每个 IP 可上邮箱数'}<input className="field mt-1 w-full" type="number" min={1} max={1000} value={capacity} onChange={event => setCapacity(Number(event.target.value) || 1)} /></label>
        <label className="text-[12px] md:col-span-2">导入后直接分配给<select className="field mt-1 w-full" value={proxyOperator} onChange={event => setProxyOperator(event.target.value)}><option value="">暂不分配</option>{operatorOptions.map(item => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label>
        <label className="text-[12px] md:col-span-4">代理列表（每行一个，最多 100 行）<textarea className="field mt-1 h-[110px] w-full font-mono" value={proxyText} onChange={event => setProxyText(event.target.value)} placeholder={'socks5://user:pass@1.2.3.4:1080\nhttp://user:pass@5.6.7.8:8080'} required /></label>
        <div className="md:col-span-4"><button className="btn btn-primary" disabled={busy !== ''}><Upload size={14} />{busy === 'proxies' ? '正在匹配平台…' : '导入并匹配'}</button></div>
      </form>
      {proxyResult && proxyResult.unmatched.length > 0 && <div className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-3 text-[12px]" role="status">
        以下 {proxyResult.unmatched.length}{' 行在平台该节点的可用代理中找不到，未保存（请确认已在 1dayApi 导入且仍有空位）：'}
        <div className="mt-1 font-mono">{proxyResult.unmatched.map(item => `第 ${item.line} 行 ${item.server}`).join('，')}</div>
      </div>}

      <div className="mt-4 flex flex-wrap items-end gap-3">
        <label className="text-[12px]">把勾选的 {selected.length}{' 个 IP 分配给'}<select className="field mt-1 w-[220px]" value={assignTo} onChange={event => setAssignTo(event.target.value)}><option value="">收回（不分配）</option>{operatorOptions.map(item => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label>
        <button className="btn btn-default" disabled={busy !== '' || selected.length === 0} onClick={() => void run('assign', async () => { await onboardingAPI.adminAssignProxies(selected, assignTo || null); setSelected([]) })}>应用</button>
      </div>
      <div className="mt-3 max-h-[360px] overflow-auto">
        <table className="w-full text-left text-[13px]">
          <thead className="border-b border-line text-ink-dim"><tr>
            <th className="p-2"><input type="checkbox" aria-label="全选" checked={overview.proxies.length > 0 && selected.length === overview.proxies.length} onChange={event => setSelected(event.target.checked ? overview.proxies.map(item => item.id) : [])} /></th>
            <th className="p-2 font-medium">{'出口 IP'}</th><th className="p-2 font-medium">代理</th><th className="p-2 font-medium">节点 / 分组</th><th className="p-2 font-medium">已用</th><th className="p-2 font-medium">上号员</th><th className="p-2 font-medium" />
          </tr></thead>
          <tbody>
            {overview.proxies.length === 0 && <tr><td className="p-2 text-ink-dim" colSpan={7}>{'IP 池是空的。'}</td></tr>}
            {overview.proxies.map(proxy => <tr key={proxy.id} className="border-b border-line last:border-0">
              <td className="p-2"><input type="checkbox" aria-label={`选择 ${proxy.exit_ip}`} checked={selected.includes(proxy.id)} onChange={event => setSelected(current => event.target.checked ? [...current, proxy.id] : current.filter(id => id !== proxy.id))} /></td>
              <td className="p-2 font-mono">{proxy.exit_ip}</td><td className="p-2 font-mono text-ink-dim">{proxy.type}://{proxy.server}</td>
              <td className="p-2">{proxy.node_name}{proxy.proxy_group ? ` / ${proxy.proxy_group}` : ''}</td><td className="p-2">{proxy.used}/{proxy.capacity}</td>
              <td className="p-2">{proxy.operator_id ? (operatorName.get(proxy.operator_id) ?? '—') : <span className="text-ink-dim">未分配</span>}</td>
              <td className="p-2">{proxy.used === 0 && <button className="btn btn-ghost" disabled={busy !== ''} onClick={() => { if (window.confirm(`从 IP 池删除 ${proxy.exit_ip}？`)) void run(`delete:${proxy.id}`, async () => { await onboardingAPI.adminDeleteProxy(proxy.id) }) }}>删除</button>}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>

    <section className="rounded-lg border border-line bg-surface p-4">
      <h2 className="font-medium">邮箱分配</h2>
      <p className="mt-1 text-ink-dim">{'批量粘贴邮箱并选择上号员，系统会把每个邮箱自动配到该上号员名下空位最多的 IP。放不下的邮箱会列出来，不会被保存。'}</p>
      <form className="mt-3 grid gap-3 md:grid-cols-2" onSubmit={importAccounts}>
        <label className="text-[12px]">上号员<select className="field mt-1 w-full" value={mailOperator} onChange={event => setMailOperator(event.target.value)} required><option value="">请选择</option>{operatorOptions.map(item => <option key={item.id} value={item.id}>{item.username}</option>)}</select></label>
        <label className="text-[12px]">归属组织<select className="field mt-1 w-full" value={mailOrg} onChange={event => setMailOrg(event.target.value)}>{orgs.map(org => <option key={org.id} value={org.id}>{org.name}</option>)}</select></label>
        <label className="text-[12px] md:col-span-2">邮箱列表（换行、逗号或分号分隔）<textarea className="field mt-1 h-[110px] w-full font-mono" value={mailText} onChange={event => setMailText(event.target.value)} required /></label>
        <div className="flex flex-wrap items-center gap-3 md:col-span-2">
          <button className="btn btn-primary" disabled={busy !== '' || emails.length === 0}><Upload size={14} />{busy === 'accounts' ? '正在分配…' : `分配 ${emails.length} 个邮箱`}</button>
          {mailOperator && <span className={emails.length > freeSlots ? 'text-danger' : 'text-ink-dim'}>{'该上号员剩余 IP 名额 '}{freeSlots}</span>}
        </div>
      </form>
      {mailResult && (mailResult.unplaced.length > 0 || mailResult.invalid.length > 0) && <div className="mt-3 rounded-lg border border-warn/40 bg-warn/10 p-3 text-[12px]" role="status">
        {[...mailResult.unplaced, ...mailResult.invalid].map(item => <div key={item.email}>{item.email}：{item.reason}</div>)}
      </div>}
      <div className="mt-3 max-h-[360px] overflow-auto">
        <table className="w-full text-left text-[13px]">
          <thead className="border-b border-line text-ink-dim"><tr><th className="p-2 font-medium">邮箱</th><th className="p-2 font-medium">上号员</th><th className="p-2 font-medium">{'出口 IP'}</th><th className="p-2 font-medium">状态</th><th className="p-2 font-medium" /></tr></thead>
          <tbody>
            {overview.accounts.length === 0 && <tr><td className="p-2 text-ink-dim" colSpan={5}>还没有邮箱。</td></tr>}
            {overview.accounts.map(account => <tr key={account.id} className="border-b border-line last:border-0">
              <td className="p-2 break-all">{account.email}</td>
              <td className="p-2">{account.operator_id ? (operatorName.get(account.operator_id) ?? '—') : <span className="text-ink-dim">未分配</span>}</td>
              <td className="p-2 font-mono">{account.proxy_id ? (proxyIp.get(account.proxy_id) ?? '—') : '—'}</td>
              <td className="p-2">{account.status ? (ACCOUNT_STATE[account.status] ?? account.status) : '未上号'}</td>
              <td className="p-2">{account.operator_id && !['submitting', 'submitted', 'completed'].includes(account.status ?? '') && <button className="btn btn-ghost" disabled={busy !== ''} onClick={() => void run(`unassign:${account.id}`, async () => { await onboardingAPI.adminUnassignAccount(account.id) })}>收回</button>}</td>
            </tr>)}
          </tbody>
        </table>
      </div>
    </section>
  </div>
}
