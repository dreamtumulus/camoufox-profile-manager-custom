import { proxyCheckTone, type Profile } from './api'

/** `grouped` folds the list into one section per group; `list` is the flat, paginated table. */
export type ProfileLayout = 'grouped' | 'list'

/** The same word the group filter and the API use for profiles without a group. */
export const UNGROUPED = '__ungrouped__'
export const LAYOUT_KEY = 'camoufox-pm.profile-layout'
export const EXPANDED_KEY = 'camoufox-pm.profile-groups-expanded'
/** Rows an open section shows before it asks, and how many each "Show more" adds. */
export const SECTION_ROW_STEP = 50
/** A search or filter opens the sections it matched only while the answer is this short. */
export const AUTO_EXPAND_LIMIT = 200

type Row = Pick<Profile, 'id' | 'group' | 'status' | 'last_used' | 'proxy_check'>

export interface ProfileSection<T> {
  /** The group ID, or `UNGROUPED`. */
  key: string
  /** Null for the ungrouped section, whose name is the caller's to translate. */
  name: string | null
  /** The profiles that pass the current filters, in the order they were given. */
  rows: T[]
  // The numbers below describe the whole group, whatever the filters hide: a
  // collapsed section has to say that something in it is running or broken.
  total: number
  running: number
  blocked: number
  proxyIssues: number
  lastUsed: string | null
}

/**
 * One section per group that still has a matching profile.
 *
 * Names sort the way a person counts — "Homes 9" before "Homes 10" — and the
 * ungrouped profiles close the list in either direction, because they are the
 * remainder rather than one more name.
 */
export function buildSections<T extends Row>(
  matched: readonly T[],
  all: readonly Row[],
  groupNames: ReadonlyMap<string, string>,
  running: ReadonlySet<string>,
  options: { descending?: boolean; locale?: string } = {},
): ProfileSection<T>[] {
  const keyOf = (row: Row) => row.group || UNGROUPED
  const sections = new Map<string, ProfileSection<T>>()
  for (const row of matched) {
    const key = keyOf(row)
    let section = sections.get(key)
    if (!section) {
      section = { key, name: key === UNGROUPED ? null : groupNames.get(key) ?? key, rows: [], total: 0, running: 0, blocked: 0, proxyIssues: 0, lastUsed: null }
      sections.set(key, section)
    }
    section.rows.push(row)
  }
  for (const row of all) {
    const section = sections.get(keyOf(row))
    if (!section) continue
    section.total += 1
    if (running.has(row.id)) section.running += 1
    if (row.status === 'blocked') section.blocked += 1
    if (row.proxy_check && proxyCheckTone(row.proxy_check) !== 'ok') section.proxyIssues += 1
    const used = row.last_used ? new Date(row.last_used).getTime() : NaN
    if (!Number.isNaN(used) && (section.lastUsed === null || used > new Date(section.lastUsed).getTime())) section.lastUsed = row.last_used ?? null
  }
  const collator = new Intl.Collator(options.locale, { numeric: true, sensitivity: 'base' })
  const direction = options.descending ? -1 : 1
  return [...sections.values()].sort((a, b) => {
    if (a.name === null) return 1
    if (b.name === null) return -1
    return collator.compare(a.name, b.name) * direction || (a.key < b.key ? -1 : 1)
  })
}

/**
 * Whether sections start open without being asked.
 *
 * A lone section has nothing to be folded against. A search or filter is a
 * request to see what matched, but only a short answer is opened: a one-letter
 * search over thousands of profiles would otherwise draw every one of them.
 */
export function opensByItself(sectionCount: number, matchedCount: number, narrowed: boolean): boolean {
  return sectionCount === 1 || (narrowed && matchedCount <= AUTO_EXPAND_LIMIT)
}

/** Grouped unless this browser chose the flat list. */
export function parseLayout(stored: string | null): ProfileLayout {
  return stored === 'list' ? 'list' : 'grouped'
}

/** The remembered open sections; anything unreadable means none, the folded start. */
export function parseExpanded(stored: string | null): string[] {
  try {
    const value: unknown = JSON.parse(stored ?? '[]')
    return Array.isArray(value) ? value.filter((key): key is string => typeof key === 'string') : []
  } catch {
    return []
  }
}
