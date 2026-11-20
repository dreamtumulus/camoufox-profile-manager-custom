import type { ProfileViewConfig } from './api'
import caseFoldData from './tag-case-fold.json'

/** Generated Unicode 15.0 casefold exceptions keep shared tag links aligned with Python. */
export function normalizeTag(value: string): string {
  const exceptions: Record<string, string> = caseFoldData.exceptions
  return [...value.normalize('NFC')].map(char => exceptions[char] ?? char.toLowerCase()).join('').trim().replace(/\s+/g, ' ')
}

/** Newest first: the profiles just added are the ones being worked on. */
export const DEFAULT_VIEW: ProfileViewConfig = { search: '', status: 'all', group: null, tags: [], sort_key: 'created', sort_direction: 'desc' }
const SORT_KEYS = ['created', 'name', 'id', 'group', 'os', 'status', 'last_used']
const STATUSES = ['all', 'active', 'inactive', 'blocked', 'maintenance']

export function readProfileView(params: URLSearchParams): { config: ProfileViewConfig; page: number } {
  const status = params.get('status') ?? 'all'
  const sort = params.get('sort')
  const descending = params.get('direction') === 'desc'
  // A link names its order unless it is the default one. Links made while the
  // default was still name order left `sort` out for it, so a `direction=desc`
  // on its own still means names descending.
  const order: Pick<ProfileViewConfig, 'sort_key' | 'sort_direction'> = sort === null
    ? descending ? { sort_key: 'name', sort_direction: 'desc' } : { sort_key: DEFAULT_VIEW.sort_key, sort_direction: DEFAULT_VIEW.sort_direction }
    : SORT_KEYS.includes(sort) ? { sort_key: sort as ProfileViewConfig['sort_key'], sort_direction: descending ? 'desc' : 'asc' } : { sort_key: DEFAULT_VIEW.sort_key, sort_direction: DEFAULT_VIEW.sort_direction }
  const tags = [...params.getAll('tags'), ...params.getAll('tag')].filter(tag => !/[\p{C},]/u.test(tag)).map(tag => normalizeTag(tag))
  const validTags = [...new Set(tags)].filter(tag => tag.length > 0 && tag.length <= 32 && !/[\p{C},]/u.test(tag))
  const page = Number(params.get('page'))
  return {
    config: { search: (params.get('search') ?? '').slice(0, 255), status: (STATUSES.includes(status) ? status : 'all') as ProfileViewConfig['status'], group: params.get('group') || null, tags: validTags.length <= 20 ? validTags : [], ...order },
    page: Number.isSafeInteger(page) && page > 0 ? page : 1,
  }
}

export function profileViewQuery(config: ProfileViewConfig, page: number, current = new URLSearchParams()): string {
  const params = new URLSearchParams(current)
  for (const key of ['search', 'status', 'group', 'tags', 'tag', 'sort', 'direction', 'page']) params.delete(key)
  if (config.search) params.set('search', config.search)
  if (config.status !== 'all') params.set('status', config.status)
  if (config.group) params.set('group', config.group)
  config.tags.forEach(tag => params.append('tags', tag))
  if (config.sort_key !== DEFAULT_VIEW.sort_key || config.sort_direction !== DEFAULT_VIEW.sort_direction) {
    params.set('sort', config.sort_key)
    if (config.sort_direction === 'desc') params.set('direction', 'desc')
  }
  if (page > 1) params.set('page', String(page))
  return params.toString()
}
