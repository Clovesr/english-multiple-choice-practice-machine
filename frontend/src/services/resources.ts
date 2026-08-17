import { api, del, get, post, put } from '../api'

// 类型与 API_CONTRACT.md §2/§3/§4 对应；后端字段来源 services/resources.py。

export interface ResourceProgress {
  last_segment_id: number | null
  scroll_ratio: number
  total_reading_ms: number
  opened_count: number
  last_opened_at: string | null
  updated_at: string
}

export interface Resource {
  id: number
  uuid: string
  title: string
  type: string
  language: string
  format: string
  status: 'inbox' | 'active' | 'archived' | 'needs_review'
  source: string
  source_url: string
  author: string
  license: string
  private_only: number
  checksum: string
  original_filename: string
  media_type: string
  size_bytes: number
  segment_count: number
  parser_name: string
  parser_version: number
  parse_error: string
  imported_at: string
  created_at: string
  updated_at: string
  progress: ResourceProgress | null
}

export interface ResourceSegment {
  id: number
  resource_id: number
  sequence: number
  kind: 'paragraph' | 'heading'
  heading_level: number
  content: string
  metadata: string
}

export interface Paged<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface SearchHit {
  resource_id: number
  resource_title: string
  segment_id: number
  sequence: number
  snippet: string
}

export interface CollectedCard {
  card_id: number
  card_type: string
  review_item_id: number
}

export interface CollectResult {
  entry: Record<string, unknown> & { id: number, term: string }
  occurrence_id: number
  cards: CollectedCard[]
  enriched_from_dictionary: boolean
  merged: boolean
}

export interface ResourceListParams {
  status?: string
  type?: string
  q?: string
  limit?: number
  offset?: number
}

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value))
  }
  const text = search.toString()
  return text ? `?${text}` : ''
}

export function listResources(params: ResourceListParams = {}) {
  return get<Paged<Resource>>(`/resources${query({ ...params })}`)
}

export function getResource(id: number) {
  return get<Resource>(`/resources/${id}`)
}

export function importResourceFile(file: File, meta: { title?: string, type?: string, language?: string } = {}) {
  const body = new FormData()
  body.append('file', file)
  if (meta.title) body.append('title', meta.title)
  if (meta.type) body.append('type', meta.type)
  if (meta.language) body.append('language', meta.language)
  return api<{ resource: Resource }>('/resources/import', { method: 'POST', body })
}

export function importResourceText(payload: {
  title: string
  content: string
  type?: string
  format?: 'txt' | 'md'
  source?: string
  author?: string
  language?: string
}) {
  return post<{ resource: Resource }>('/resources/import', payload)
}

export function updateResource(id: number, patch: Partial<Pick<Resource, 'title' | 'status' | 'source' | 'author' | 'language'>>) {
  return put<Resource>(`/resources/${id}`, patch)
}

export function deleteResource(id: number) {
  return del<void>(`/resources/${id}`)
}

export function getSegments(id: number, offset = 0, limit = 500) {
  return get<Paged<ResourceSegment>>(`/resources/${id}/segments${query({ offset, limit })}`)
}

/** 拉全量段落（W1 文本资源规模下逐页取齐）。 */
export async function getAllSegments(id: number): Promise<ResourceSegment[]> {
  const first = await getSegments(id, 0)
  const segments = [...first.items]
  while (segments.length < first.total) {
    const page = await getSegments(id, segments.length)
    if (!page.items.length) break
    segments.push(...page.items)
  }
  return segments
}

export function saveProgress(id: number, patch: { last_segment_id?: number | null, scroll_ratio: number, reading_ms_delta?: number }) {
  return put<{ progress: ResourceProgress }>(`/resources/${id}/progress`, patch)
}

export function searchAll(q: string, offset = 0, limit = 50) {
  return get<Paged<SearchHit>>(`/search${query({ q, scope: 'resources', offset, limit })}`)
}

export function rebuildSearch() {
  return post<{ job: string, segments: number }>('/search/rebuild')
}

/** API_CONTRACT §4；后端未实现前会返回 404，调用方需给出友好降级。 */
export function collectWordFromSelection(payload: {
  term: string
  context_sentence: string
  context_before?: string
  context_after?: string
  resource_id: number
  segment_id: number
}) {
  return post<CollectResult>('/vocabulary/from-selection', payload)
}

export const RESOURCE_STATUS_LABELS: Record<Resource['status'], string> = {
  inbox: '收件箱',
  active: '学习中',
  archived: '已归档',
  needs_review: '待处理',
}

export const RESOURCE_TYPE_LABELS: Record<string, string> = {
  article: '文章',
  book: '书籍',
  note: '笔记',
  paper_text: '真题文本',
  other: '其他',
}
