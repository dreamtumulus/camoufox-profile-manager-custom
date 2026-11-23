'use client'

import { useState } from 'react'

import { useI18n } from '@/components/i18n-provider'
import type { Options, Preset, ProxyMode } from '@/lib/auto-onboarding-api'
import { LIMIT_MAX, parseLimit, summarize, validUuid } from '@/lib/auto-onboarding-preset'

const MODES: ProxyMode[] = ['row', 'auto', 'pool']

/**
 * The three steps of the access preset: how the account is authorized, where
 * it goes out, and whom it belongs to. Every choice is handed to `onChange` as
 * it is made; text is handed over when the field is left.
 */
export function PresetSteps({
  preset,
  options,
  disabled,
  onChange,
}: {
  preset: Preset
  options: Options
  disabled: boolean
  onChange: (patch: Partial<Preset>) => void
}) {
  const { t } = useI18n()
  const pool = options.pool && options.pool.node_id === preset.node_id ? options.pool : null
  const org = options.orgs.find(item => item.id === preset.org_id)
  const summary = summarize(preset, options)

  const modeLabel: Record<ProxyMode, string> = {
    row: t('Each row’s own exit'),
    auto: t('Assigned by the platform'),
    pool: t('Pick from the pool ({count})', { count: pool?.proxies.length ?? 0 }),
  }
  const modeHint: Record<ProxyMode, string> = {
    row: t('Each row is onboarded through the pool proxy that comes out at the exit IP of its own browser proxy. A row whose exit is not found, matches several proxies or is full cannot be given an authorize link. Whether another organization already holds an exit cannot be seen beforehand: the platform decides when the link is made, and a refusal is shown on that row.'),
    auto: t('The platform picks a usable proxy for every row, the one with the fewest accounts first; with a group chosen it picks only inside that group. The account then always goes through the proxy the platform picked, which may differ from the exit the row’s browser signed in from.'),
    pool: t('Every row is put on this one proxy. A proxy takes at most 3 accounts by default; when it runs out, the platform refuses to make a link for later rows. The account then always goes through this proxy, which may differ from the exit the row’s browser signed in from.'),
  }
  const proxyText = summary.proxy.kind === 'row' ? t('each row’s own exit')
    : summary.proxy.kind === 'auto'
      ? summary.proxy.group ? t('assigned within group {group}', { group: summary.proxy.group }) : t('assigned by the platform')
      : summary.proxy.exitIp ?? t('from the pool, not picked')
  const orgText = summary.org.kind === 'profile'
    ? `${summary.org.name}${summary.org.domain ? ` · @${summary.org.domain}` : summary.org.needsDomain ? ` · ${t('no domain chosen')}` : ''}`
    : summary.org.kind === 'unspecified' ? t('unspecified')
    : summary.org.kind === 'uuid' ? summary.org.short ?? t('UUID not entered')
    : '—'

  return (
    <>
      <Step number={1} title={t('Credentials')}>
        <span className="inline-block rounded-md bg-raised px-2.5 py-1.5 font-medium">{t('Authorize link')}</span>
        <p className="mt-2 text-[12px] text-ink-dim">{t('Pressing “Make authorize link” on a row has the platform make a link for it, which is opened in a new tab of that row’s browser. Consent there, then paste back the authorization code the page shows.')}</p>
      </Step>

      <Step number={2} title={t('Exit')}>
        <label className="field-label" htmlFor="onboard-node">{t('Exit node')}</label>
        <select id="onboard-node" className="field" value={preset.node_id ?? ''} disabled={disabled} onChange={event => onChange({ node_id: event.target.value, proxy_group: null, proxy_id: null })}>
          {options.nodes.length === 0 && <option value="">{t('This token reaches no node')}</option>}
          {options.nodes.map(node => (
            <option key={node.id} value={node.id}>{`${node.name} · ${node.online ? t('online') : t('offline')} · ${node.used}/${node.capacity}`}</option>
          ))}
        </select>
        <p className="mt-1 text-[12px] text-ink-dim">{t('Everything the account requests while it runs goes out through this node.')}</p>

        <div className="field-label mt-3" id="onboard-proxy-label">{t('Proxy')}</div>
        <div className="inline-flex flex-wrap gap-0.5 rounded-md bg-raised p-0.5" role="group" aria-labelledby="onboard-proxy-label">
          {MODES.map(mode => (
            <button
              key={mode}
              type="button"
              data-proxy={mode}
              aria-pressed={preset.proxy_mode === mode}
              disabled={disabled}
              className={`rounded px-2.5 py-1.5 text-[12px] ${preset.proxy_mode === mode ? 'bg-surface font-medium text-ink shadow-[0_0_0_1px_var(--color-line)]' : 'text-ink-dim hover:text-ink'}`}
              onClick={() => onChange({ proxy_mode: mode })}
            >
              {modeLabel[mode]}
            </button>
          ))}
        </div>
        {preset.proxy_mode === 'auto' && (
          <>
            <label className="field-label mt-3" htmlFor="onboard-proxy-group">{t('Proxy group')}</label>
            <select id="onboard-proxy-group" className="field" value={preset.proxy_group ?? ''} disabled={disabled} onChange={event => onChange({ proxy_group: event.target.value || null })}>
              <option value="">{t('Any group')}</option>
              {(pool?.groups ?? []).map(group => (
                <option key={group.name} value={group.name}>{t('{name} · assignable {count}', { name: group.name, count: group.assignable })}</option>
              ))}
            </select>
          </>
        )}
        {preset.proxy_mode === 'pool' && (
          <>
            <label className="field-label mt-3" htmlFor="onboard-pool-proxy">{t('Pool proxy')}</label>
            <select id="onboard-pool-proxy" className="field" value={preset.proxy_id ?? ''} disabled={disabled} onChange={event => onChange({ proxy_id: event.target.value || null })}>
              <option value="">{t('Choose a proxy…')}</option>
              {(pool?.proxies ?? []).map(proxy => (
                <option key={proxy.id} value={proxy.id}>{t('{exit} · {group} · can take {count} more', { exit: proxy.exit_ip ?? proxy.id, group: proxy.group ?? t('no group'), count: proxy.slots })}</option>
              ))}
            </select>
          </>
        )}
        <p className="mt-1.5 text-[12px] text-ink-dim" data-testid="proxy-hint">{modeHint[preset.proxy_mode]}</p>
        <p className="mt-1 text-[12px] text-ink-dim">{t('A proxy takes two accounts by default; the third place is used only when the regular ones run out. Once bound, an account stays on its proxy.')}</p>
      </Step>

      <Step number={3} title={t('Ownership and limits')}>
        {options.orgs.length ? (
          <>
            <label className="field-label" htmlFor="onboard-org">{t('Organization')}</label>
            <select id="onboard-org" className="field" value={preset.org_id ?? ''} disabled={disabled} onChange={event => onChange({ org_id: event.target.value, domain: null })}>
              {options.orgs.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
            <p className="mt-1 text-[12px] text-ink-dim">{t('Choose one of the organization profiles this onboarder token may use. One must be chosen.')}</p>
            {org && org.domains.length > 0 && (
              <>
                <label className="field-label mt-3" htmlFor="onboard-domain">{t('Email domain')}</label>
                <select id="onboard-domain" className="field" value={preset.domain ?? ''} disabled={disabled} onChange={event => onChange({ domain: event.target.value || null })}>
                  {org.domains.length > 1 && <option value="">{t('Choose a domain…')}</option>}
                  {org.domains.map(domain => <option key={domain} value={domain}>{`@${domain}`}</option>)}
                </select>
                <p className="mt-1 text-[12px] text-ink-dim">{t('The account’s address must be under this domain, or the platform refuses it and it does not join the pool.')}</p>
              </>
            )}
          </>
        ) : (
          <>
            <label className="field-label" htmlFor="onboard-org-mode">{t('Organization (optional)')}</label>
            <select id="onboard-org-mode" className="field" value={preset.org_manual ? 'manual' : ''} disabled={disabled} onChange={event => onChange({ org_manual: event.target.value === 'manual' })}>
              <option value="">{t('Unspecified: recognized on a clean exit; accounts of different organizations each become one')}</option>
              <option value="manual">{t('Enter a UUID…')}</option>
            </select>
            {preset.org_manual && (
              <TextField
                id="onboard-org-uuid"
                label={t('Organization UUID')}
                value={preset.org_uuid ?? ''}
                placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
                disabled={disabled}
                problem={text => (text.trim() && !validUuid(text) ? t('An organization UUID looks like xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx.') : null)}
                onCommit={text => onChange({ org_uuid: text.trim() || null })}
              />
            )}
            <p className="mt-1 text-[12px] text-ink-dim">{t('This onboarder token has no organization profiles: leave the organization unspecified, or enter its UUID.')}</p>
            {!preset.org_manual && (
              <p className="mt-1 text-[12px] text-ink-dim" data-testid="virgin-hint">{t('Left unspecified, the platform needs an exit that carries no account yet (while it binds organizations to exits): it assigns only such exits, and an exit named for the row that already carries one is refused when the link is made.')}</p>
            )}
          </>
        )}

        <div className="field-label mt-3">{t('Account group')}</div>
        <div className="rounded-md border border-dashed border-line px-2.5 py-1.5" data-testid="account-group">{t('Default group')}</div>
        <p className="mt-1 text-[12px] text-ink-dim">{t('Accounts onboarded with an onboarder token always join the platform’s default group. It cannot be chosen.')}</p>

        <Advanced preset={preset} disabled={disabled} onChange={onChange} />
      </Step>

      <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-line px-4 py-3 text-[12px]" data-testid="preset-summary">
        {[
          [t('Method'), t('authorize link')],
          [t('Node'), summary.node ?? '—'],
          [t('Proxy'), proxyText],
          [t('Organization'), orgText],
          [t('Account group'), t('default group')],
        ].map(([name, value]) => (
          <span key={name}><span className="mr-1 text-ink-dim">{name}</span> {value}</span>
        ))}
      </div>
    </>
  )
}

function Step({ number, title, children }: { number: number; title: string; children: React.ReactNode }) {
  return (
    <div className="border-b border-line px-4 py-3 last:border-b-0" data-step={number}>
      <div className="mb-2.5 flex items-center gap-2 font-medium">
        <span className="grid h-5 w-5 place-items-center rounded-full bg-signal-shade text-[11px] text-signal">{number}</span>
        {title}
      </div>
      {children}
    </div>
  )
}

/** The note and the two limits. What is set is said beside the title, so it shows when closed. */
function Advanced({ preset, disabled, onChange }: { preset: Preset; disabled: boolean; onChange: (patch: Partial<Preset>) => void }) {
  const { t } = useI18n()
  const set = [
    preset.concurrency_limit !== null ? t('concurrency {value}', { value: preset.concurrency_limit }) : null,
    preset.rpm_limit !== null ? t('RPM {value}', { value: preset.rpm_limit }) : null,
    preset.note ? t('has a note') : null,
  ].filter(Boolean)
  const limitProblem = (text: string) => (parseLimit(text).ok ? null : t('A whole number from 0 to {max}; leave empty for the platform default.', { max: LIMIT_MAX }))
  const commitLimit = (key: 'concurrency_limit' | 'rpm_limit') => (text: string) => {
    const parsed = parseLimit(text)
    if (parsed.ok) onChange({ [key]: parsed.value })
  }
  return (
    <details className="mt-3 rounded-md border border-line" data-testid="advanced">
      <summary className="cursor-pointer px-3 py-2 font-medium">
        {t('Advanced')} <span className="font-normal text-ink-dim" data-testid="advanced-summary">{set.length ? t('· set: {items}', { items: set.join(' · ') }) : t('· note, limits')}</span>
      </summary>
      <div className="grid gap-3 px-3 pb-3 sm:grid-cols-2">
        <div>
          <div className="field-label">{t('Name')}</div>
          <div className="rounded-md border border-dashed border-line px-2.5 py-1.5">{t('Each row uses its own email address')}</div>
        </div>
        <TextField id="onboard-note" label={t('Note (optional)')} value={preset.note ?? ''} maxLength={500} disabled={disabled} problem={() => null} onCommit={text => onChange({ note: text.trim() || null })} />
        <TextField
          id="onboard-concurrency"
          label={t('Concurrency limit')}
          value={preset.concurrency_limit === null ? '' : String(preset.concurrency_limit)}
          placeholder={t('Platform default')}
          hint={t('The most requests the account may have in flight. Empty = the platform default.')}
          inputMode="numeric"
          disabled={disabled}
          problem={limitProblem}
          onCommit={commitLimit('concurrency_limit')}
        />
        <TextField
          id="onboard-rpm"
          label={t('RPM limit')}
          value={preset.rpm_limit === null ? '' : String(preset.rpm_limit)}
          placeholder={t('Platform default')}
          hint={t('The most requests per minute. Empty = the platform default.')}
          inputMode="numeric"
          disabled={disabled}
          problem={limitProblem}
          onCommit={commitLimit('rpm_limit')}
        />
      </div>
    </details>
  )
}

/**
 * A text field that keeps what is typed to itself until it is left.
 *
 * What does not fit is said under the field and not handed over, so a
 * half-typed number or UUID never replaces the saved value.
 */
function TextField({
  id,
  label,
  value,
  placeholder,
  hint,
  maxLength,
  inputMode,
  disabled,
  problem,
  onCommit,
}: {
  id: string
  label: string
  value: string
  placeholder?: string
  hint?: string
  maxLength?: number
  inputMode?: 'numeric'
  disabled: boolean
  problem: (text: string) => string | null
  onCommit: (text: string) => void
}) {
  const [typed, setTyped] = useState<string | null>(null)
  const text = typed ?? value
  const wrong = typed === null ? null : problem(typed)
  return (
    <div>
      <label className="field-label" htmlFor={id}>{label}</label>
      <input
        id={id}
        className="field"
        type="text"
        autoComplete="off"
        spellCheck={false}
        inputMode={inputMode}
        maxLength={maxLength}
        placeholder={placeholder}
        value={text}
        disabled={disabled}
        aria-invalid={wrong ? true : undefined}
        onChange={event => setTyped(event.target.value)}
        onBlur={() => {
          if (typed === null || problem(typed)) return
          if (typed.trim() !== value) onCommit(typed)
          setTyped(null)
        }}
        onKeyDown={event => { if (event.key === 'Enter') event.currentTarget.blur() }}
      />
      {wrong ? <p className="mt-1 text-[12px] text-danger" role="alert">{wrong}</p> : hint && <p className="mt-1 text-[12px] text-ink-dim">{hint}</p>}
    </div>
  )
}
