'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Copy, LoaderCircle, LogOut, RefreshCw } from 'lucide-react'
import { useToast } from '@/components/toast'
import { onboardingAPI, type OnboardingConfig, type OperatorBundle, type OperatorSession, type OperatorTask } from '@/lib/onboarding-api'
import { accountOnboardingState, type AccountOnboardingState } from '@/lib/onboarding-flow'

const STATE_LABELS: Record<AccountOnboardingState, string> = {
  not_started: '未上号', in_progress: '进行中', ready_to_submit: '待提交 SK', completed: '已上号', failed: '失败（可重试）', closed_incomplete: '已关闭未完成',
}
const STATE_TONE: Record<AccountOnboardingState, string> = {
  not_started: 'text-ink-dim', in_progress: 'text-warn', ready_to_submit: 'text-warn', completed: 'text-ok', failed: 'text-danger', closed_incomplete: 'text-ink-dim',
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

/** What a signed-in operator sees: only the e-mails and exit IPs assigned to them. */
export function OperatorPanel({ config, onConfigChange }: { config: OnboardingConfig; onConfigChange: (config: OnboardingConfig) => void }) {
  const toast = useToast()
  const signedIn = config.configured && config.mode === 'operator'
  const [serverUrl, setServerUrl] = useState(config.server_url ?? '')
  const [editServer, setEditServer] = useState(!config.server_url)
  const [username, setUsername] = useState(config.operator?.username ?? '')
  const [password, setPassword] = useState('')
  const [bundle, setBundle] = useState<OperatorBundle | null>(null)
  const [filter, setFilter] = useState<'todo' | 'all' | AccountOnboardingState>('todo')
  const [busy, setBusy] = useState('')
  const [error, setError] = useState<string | null>(null)
  const busyRef = useRef(busy)
  useEffect(() => { busyRef.current = busy }, [busy])

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setBusy('refresh')
    try {
      setBundle(await onboardingAPI.operatorBundle())
      if (!quiet) setError(null)
    } catch (err) {
      setError(message(err))
      // A rejected login clears the stored token; ask the app who we are now.
      try { onConfigChange(await onboardingAPI.config()) } catch { /* keep the original error */ }
    } finally { if (!quiet) setBusy('') }
  }, [onConfigChange])

  useEffect(() => {
    if (!signedIn) return
    // Loaded outside the effect body so the first paint is not blocked on it.
    const first = setTimeout(() => { void refresh() }, 0)
    const timer = setInterval(() => { if (!busyRef.current) void refresh(true) }, 60000)
    return () => { clearTimeout(first); clearInterval(timer) }
  }, [signedIn, refresh])

  // A window that is open is polled for the session key; nothing is submitted automatically.
  const waiting = useMemo(() => bundle?.tasks.filter(task => task.session?.status === 'browser_open' && task.session.browser_running).map(task => task.session!.id) ?? [], [bundle])
  const waitingKey = waiting.join(',')
  useEffect(() => {
    if (!waitingKey) return
    let cancelled = false
    const timer = setInterval(async () => {
      for (const id of waitingKey.split(',')) {
        try {
          const answer = await onboardingAPI.extract(id, true)
          if (cancelled) return
          if (answer.session.status === 'extracted') {
            toast('ok', '提取完SK', answer.session.email)
            void refresh(true)
          }
        } catch { /* The manual button reports the reason. */ }
      }
    }, 5000)
    return () => { cancelled = true; clearInterval(timer) }
  }, [waitingKey, refresh, toast])

  async function signIn(event: React.FormEvent) {
    event.preventDefault()
    setBusy('login')
    setError(null)
    try {
      const next = await onboardingAPI.operatorLogin({
        username: username.trim(), password,
        ...(editServer && serverUrl.trim() ? { server_url: serverUrl.trim() } : {}),
      })
      setPassword('')
      onConfigChange(next)
    } catch (err) { setError(message(err)) }
    finally { setBusy('') }
  }

  async function signOut() {
    if (bundle?.tasks.some(task => task.session?.browser_running) && !window.confirm('还有上号窗口未关闭，确定退出登录吗？窗口不会自动关闭。')) return
    setBusy('logout')
    try {
      setBundle(null)
      onConfigChange(await onboardingAPI.operatorLogout())
    } catch (err) { setError(message(err)) }
    finally { setBusy('') }
  }

  async function act(key: string, work: () => Promise<unknown>, done?: string) {
    setBusy(key)
    setError(null)
    try {
      await work()
      if (done) toast('ok', done)
    } catch (err) { setError(message(err)) }
    finally {
      setBusy('')
      await refresh(true)
    }
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      toast('ok', '已复制', text)
    } catch (err) { setError(`复制失败，请手动选择复制：${message(err)}`) }
  }

  if (!signedIn) {
    return <form className="mx-auto mt-6 max-w-[420px] rounded-lg border border-line bg-surface p-5" onSubmit={signIn}>
      <h2 className="font-medium">上号员登录</h2>
      <p className="mt-1 text-ink-dim">{'登录后会自动下发分配给你的邮箱和出口 IP，无需手动添加代理。'}</p>
      {error && <div className="mt-3 whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-3 text-danger" role="alert">{error}</div>}
      <div className="mt-4 grid gap-3">
        {editServer
          ? <label className="text-[12px]">服务器地址<input className="field mt-1 w-full" value={serverUrl} onChange={event => setServerUrl(event.target.value)} placeholder="https://onboarding.example.com" required /></label>
          : <p className="text-[12px] text-ink-dim">服务器：{config.server_url} <button type="button" className="underline" onClick={() => setEditServer(true)}>更改</button></p>}
        <label className="text-[12px]">账号<input className="field mt-1 w-full" value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" required /></label>
        <label className="text-[12px]">密码<input className="field mt-1 w-full" type="password" value={password} onChange={event => setPassword(event.target.value)} autoComplete="current-password" required /></label>
      </div>
      <button className="btn btn-primary mt-4 w-full" disabled={busy === 'login'}>{busy === 'login' ? '正在登录…' : '登录'}</button>
    </form>
  }

  const tasks = bundle?.tasks ?? []
  const state = (task: OperatorTask) => accountOnboardingState(task.session ?? undefined)
  const counts = tasks.reduce<Record<string, number>>((all, task) => { all[state(task)] = (all[state(task)] ?? 0) + 1; return all }, {})
  const shown = tasks.filter(task => filter === 'all' || (filter === 'todo' ? state(task) !== 'completed' : state(task) === filter))
  const openWindows = tasks.filter(task => task.session?.browser_running).length

  function actions(task: OperatorTask) {
    const session: OperatorSession | null = task.session
    const current = state(task)
    const key = (name: string) => `${name}:${task.id}`
    const disabled = busy !== ''
    if (current === 'completed') {
      return <>
        {session?.status === 'submitted' && <button className="btn btn-default" disabled={disabled} onClick={() => void act(key('result'), () => onboardingAPI.result(session.id))}>刷新结果</button>}
        {session?.browser_running && <button className="btn btn-default" disabled={disabled} onClick={() => void act(key('close'), () => onboardingAPI.close(session.id), '窗口已关闭')}>关闭窗口</button>}
      </>
    }
    if (session && session.browser_running) {
      return <>
        {session.status === 'extracted'
          ? <button className="btn btn-primary" disabled={disabled} onClick={() => void act(key('submit'), () => onboardingAPI.submit(session.id), 'SK 已提交')}>{busy === key('submit') ? '正在提交…' : '提交 SK'}</button>
          : <button className="btn btn-primary" disabled={disabled} onClick={() => void act(key('extract'), () => onboardingAPI.extract(session.id), '提取完SK')}>{busy === key('extract') ? '正在提取…' : '提取 SK'}</button>}
        <button className="btn btn-default" disabled={disabled} onClick={() => void act(key('close'), () => onboardingAPI.close(session.id), '窗口已关闭')}>关闭窗口</button>
      </>
    }
    if (session && session.status === 'extracted') {
      return <button className="btn btn-primary" disabled={disabled} onClick={() => void act(key('submit'), () => onboardingAPI.submit(session.id), 'SK 已提交')}>{busy === key('submit') ? '正在提交…' : '提交 SK'}</button>
    }
    return <>
      {session?.profile_id && current !== 'failed' && <button className="btn btn-default" disabled={disabled} onClick={() => void act(key('retry'), () => onboardingAPI.retryBrowser(session.id))}>重开原窗口</button>}
      <button className="btn btn-primary" disabled={disabled || !task.proxy_id} onClick={() => void act(key('start'), () => onboardingAPI.startTask(task.id), '窗口已打开，请在窗口中完成登录')}>
        {busy === key('start') ? <><LoaderCircle className="animate-spin" size={14} />正在打开…</> : session ? '重新上号' : '开始上号'}
      </button>
    </>
  }

  return <section>
    <div className="mb-4 flex flex-wrap items-center gap-3 rounded-lg border border-line bg-surface p-4">
      <div>
        <h2 className="font-medium">我的上号任务 · {config.operator?.username}</h2>
        <p className="mt-1 text-ink-dim">共 {tasks.length} 个邮箱 · 已上号 {counts.completed ?? 0}{' · 出口 IP '}{bundle?.proxies.length ?? 0} 个 · 打开的窗口 {openWindows}</p>
      </div>
      <button className="btn btn-default ml-auto" disabled={busy !== ''} onClick={() => void refresh()}><RefreshCw size={14} />同步任务</button>
      <button className="btn btn-default" disabled={busy !== ''} onClick={() => void signOut()}><LogOut size={14} />退出登录</button>
    </div>

    {error && <div className="mb-4 whitespace-pre-wrap break-words rounded-lg border border-danger/40 bg-danger/10 p-3 text-danger" role="alert">{error}</div>}

    <div className="mb-3 flex flex-wrap items-center gap-2">
      {([['todo', `待处理 ${tasks.length - (counts.completed ?? 0)}`], ['all', `全部 ${tasks.length}`], ['completed', `已上号 ${counts.completed ?? 0}`], ['failed', `失败 ${counts.failed ?? 0}`]] as const).map(([value, label]) =>
        <button key={value} className={`btn ${filter === value ? 'btn-primary' : 'btn-default'}`} onClick={() => setFilter(value)}>{label}</button>)}
    </div>

    {bundle === null
      ? <div className="p-6 text-ink-dim" role="status"><LoaderCircle className="inline animate-spin" size={15} /> 正在同步任务…</div>
      : shown.length === 0
        ? <div className="rounded-lg border border-line bg-surface p-6 text-ink-dim">{tasks.length === 0 ? '管理员还没有给你分配邮箱。分配后点击“同步任务”即可看到。' : '这个筛选下没有任务。'}</div>
        : <div className="overflow-x-auto rounded-lg border border-line bg-surface">
          <table className="w-full text-left text-[13px]">
            <thead className="border-b border-line text-ink-dim"><tr><th className="p-3 font-medium">邮箱</th><th className="p-3 font-medium">{'出口 IP'}</th><th className="p-3 font-medium">组织</th><th className="p-3 font-medium">状态</th><th className="p-3 font-medium">操作</th></tr></thead>
            <tbody>
              {shown.map(task => {
                const current = state(task)
                return <tr key={task.id} className="border-b border-line last:border-0 align-top">
                  <td className="p-3"><span className="break-all">{task.email}</span> <button className="btn btn-ghost" title="复制邮箱" aria-label={`复制邮箱 ${task.email}`} onClick={() => void copy(task.email)}><Copy size={13} /></button></td>
                  <td className="p-3 font-mono">{task.exit_ip ?? <span className="text-danger">{'IP 已收回'}</span>}</td>
                  <td className="p-3">{task.org_name}</td>
                  <td className="p-3">
                    <span className={STATE_TONE[current]}>{STATE_LABELS[current]}</span>
                    {task.session?.sk_masked && <div className="mt-1 font-mono text-[11px] text-ink-dim">{task.session.sk_masked}</div>}
                    {task.session?.error && <div className="mt-1 max-w-[320px] whitespace-pre-wrap break-words text-[12px] text-danger">{task.session.error}</div>}
                  </td>
                  <td className="p-3"><div className="flex flex-wrap gap-2">{actions(task)}</div></td>
                </tr>
              })}
            </tbody>
          </table>
        </div>}
  </section>
}
