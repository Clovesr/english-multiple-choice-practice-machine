import { del, get, post, put } from '../api'

// 类型与 API_CONTRACT.md §7 对应。端点由 Codex 在 Issue #2 交付；
// 本模块先行提供契约类型与纯判分逻辑（可单测），页面接线等端点就绪。

export type CardType = 'forward' | 'reverse' | 'listening' | 'spelling' | 'cloze' | 'collocation'
export type CardState = 'new' | 'learning' | 'review' | 'relearning'
export type Rating = 1 | 2 | 3 | 4

export interface StudyEntry {
  lemma: string
  phonetic_uk: string
  phonetic_us: string
  senses: Array<{ pos: string, gloss_zh: string, gloss_en?: string }>
  memory_hint?: string
  note?: string
}

export interface StudyCard {
  card_id: number
  entry_id?: number
  review_item_id: number
  card_type: CardType
  state: CardState
  entry: StudyEntry
  prompt: { text?: string, tts_text?: string, cloze_sentence?: string, pairs?: Array<{ left: string, right: string }> }
  answer: { text?: string, accept?: string[], distractors?: string[] }
  contexts: Array<{ sentence: string, source: string }>
}

export interface StudySession {
  session_id: string
  counts: { new_remaining: number, due_remaining: number, done_today: number }
  cards: StudyCard[]
}

export interface GradeResult {
  card_id: number
  review_item: Record<string, unknown>
  review_log_id: number
  next_due_at: string
  auto_correct: boolean | null
  final_rating: Rating
  attempt_id: string
}

export interface StudySettings {
  daily_new: number
  daily_review_max: number
  enabled_card_types: CardType[]
  new_card_order: 'frequency' | 'sequence' | 'random'
  leech_threshold: number
  backlog_mode: string
}

export interface StudyOverview {
  today: { new_done: number, new_target: number, reviews_done: number, due_left: number }
  overdue_total: number
  streak_days: number
  retention_7d: number | null
  retention_30d: number | null
  forecast_7d: Array<{ date: string, due: number }>
  leeches: number
}

export interface Wordbook {
  id: number
  uuid: string
  name: string
  kind: 'builtin' | 'imported'
  source_tag: string
  total: number
  state_counts: { new: number, learning: number, known: number, ignored: number, paused: number }
  active_plan: Record<string, unknown> | null
}

// —— API（契约函数；后端未就绪时调用方需处理 404/405） ——

export const getStudySession = (limit = 20) => get<StudySession>(`/study/session?limit=${limit}`)
export const gradeStudyCard = (cardId: number, body: { attempt_id: string, rating: Rating, answer_given?: string, duration_ms: number }) =>
  post<GradeResult>(`/study/cards/${cardId}/grade`, body)
export const suspendStudyCard = (cardId: number) => post<void>(`/study/cards/${cardId}/suspend`)
export const unsuspendStudyCard = (cardId: number) => post<void>(`/study/cards/${cardId}/unsuspend`)
export const getStudySettings = () => get<StudySettings>('/study/settings')
export const putStudySettings = (settings: Partial<StudySettings>) => put<StudySettings>('/study/settings', settings)
export const getStudyOverview = () => get<StudyOverview>('/study/overview')
export const listWordbooks = () => get<{ items: Wordbook[] }>('/wordbooks')
export const activateWordbookPlan = (id: number, body: { daily_new: number, new_order?: string }) =>
  post<unknown>(`/wordbooks/${id}/plan`, body)
export const deactivateWordbookPlan = (id: number) => del<void>(`/wordbooks/${id}/plan`)
export const lookupDictionary = (term: string) => get<{ found: boolean, entry: Record<string, unknown> | null }>(`/dictionary/lookup?term=${encodeURIComponent(term)}`)

// —— 纯逻辑（单测覆盖） ——

/** 生成作答幂等键（契约 §1：attempt_id 为 UUID）。后端按 UUID 校验，fallback 必须是合法 RFC 4122 v4。 */
export function newAttemptId(): string {
  const cryptoApi: Partial<Crypto> | undefined = typeof crypto !== 'undefined' ? crypto : undefined
  if (typeof cryptoApi?.randomUUID === 'function') return cryptoApi.randomUUID()
  const bytes = new Uint8Array(16)
  if (typeof cryptoApi?.getRandomValues === 'function') {
    cryptoApi.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** 反向卡无干扰项时按"主动回忆"处理（契约 004 §4.3：主观自评，不渲染单选项假选择题）。 */
export function isRecallReverse(card: Pick<StudyCard, 'card_type' | 'answer'>): boolean {
  return card.card_type === 'reverse' && !(card.answer.distractors?.length)
}

/**
 * 前端即时判定用的规范化：小写、Unicode NFKC、去首尾空白、压缩连续空白。
 * 与后端复判规则同源（004 §4.3）；后端结论为最终权威。
 */
export function normalizeAnswer(raw: string): string {
  return raw.normalize('NFKC').toLowerCase().trim().replace(/\s+/g, ' ')
}

/** 本地即时判定：answer_given 是否命中 accept 列表（accept 由后端展开）。 */
export function judgeLocally(answerGiven: string, accept: string[] | undefined): boolean | null {
  if (!accept || !accept.length) return null // 无客观答案（如正向自评卡）
  const given = normalizeAnswer(answerGiven)
  if (!given) return false
  return accept.some((candidate) => normalizeAnswer(candidate) === given)
}

/** 客观卡自动建议评分：错→Again(1)，对→Good(3)（004 §4.3）。 */
export function suggestedRating(correct: boolean): Rating {
  return correct ? 3 : 1
}

/** 该卡型是否属于客观判分（拼写/听音/搭配/选择型反向由 accept 决定）。 */
export function isObjectiveCard(card: Pick<StudyCard, 'card_type' | 'answer'>): boolean {
  if (card.card_type === 'forward') return false
  return Boolean(card.answer.accept?.length || card.answer.distractors?.length)
}

/** 键盘评分映射（1-4 数字键；契约 §7.2 四键）。 */
export function ratingFromKey(key: string): Rating | null {
  return key === '1' || key === '2' || key === '3' || key === '4' ? (Number(key) as Rating) : null
}

/** 会话排序：先复习后新学（百词斩式两阶段；后端排序落地前由前端保证）。 */
export function sortReviewsFirst(cards: StudyCard[]): StudyCard[] {
  const reviews = cards.filter((card) => card.state !== 'new')
  const news = cards.filter((card) => card.state === 'new')
  return [...reviews, ...news]
}

/** 同词多卡时的取卡优先级：到期复习卡优先；新卡按 认词>回忆>听音>拼写>挖空>搭配（本体先行）。 */
const NEW_CARD_PRIORITY: Record<CardType, number> = {
  forward: 0, reverse: 1, listening: 2, spelling: 3, cloze: 4, collocation: 5,
}

export function pickCardForWord(cards: StudyCard[]): StudyCard {
  const review = cards.find((card) => card.state !== 'new')
  if (review) return review
  return [...cards].sort((a, b) => NEW_CARD_PRIORITY[a.card_type] - NEW_CARD_PRIORITY[b.card_type])[0]
}

/** 学习队列项：drill=当日重练副本（墨墨式内循环，不写长期调度）。 */
export interface QueueItem {
  card: StudyCard
  drill: boolean
  drillCount: number
}

/**
 * 错词重练插入：把卡片副本插到 gap 张之后（不足则队尾）。
 * 当天首次评分已写入 FSRS；重练副本只做本地清障，最多 maxDrills 次。
 */
export function insertDrill(
  queue: QueueItem[],
  item: QueueItem,
  gap = 4,
  maxDrills = 2,
): QueueItem[] {
  if (item.drillCount >= maxDrills) return queue
  const copy: QueueItem = { card: item.card, drill: true, drillCount: item.drillCount + 1 }
  const index = Math.min(gap, queue.length)
  return [...queue.slice(0, index), copy, ...queue.slice(index)]
}

/** 把句子按目标词（原形+变形）切成命中/未命中片段，供模板安全高亮（不用 v-html）。 */
export function highlightSegments(
  sentence: string,
  targets: string[],
): Array<{ text: string, hit: boolean }> {
  const cleaned = targets.map((t) => t.trim()).filter(Boolean)
  if (!sentence || !cleaned.length) return sentence ? [{ text: sentence, hit: false }] : []
  const escaped = cleaned
    .sort((a, b) => b.length - a.length)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const pattern = new RegExp(`\\b(${escaped.join('|')})\\b`, 'gi')
  const segments: Array<{ text: string, hit: boolean }> = []
  let cursor = 0
  for (const match of sentence.matchAll(pattern)) {
    const index = match.index ?? 0
    if (index > cursor) segments.push({ text: sentence.slice(cursor, index), hit: false })
    segments.push({ text: match[0], hit: true })
    cursor = index + match[0].length
  }
  if (cursor < sentence.length) segments.push({ text: sentence.slice(cursor), hit: false })
  return segments
}

/** 渐进提示（百词斩式梯度披露）：一级=首字母+长度，二级=音标。 */
export function buildHint(level: number, lemma: string, phonetic: string): string | null {
  if (level <= 0) return null
  const shape = `${lemma.charAt(0)}${'·'.repeat(Math.max(lemma.length - 1, 0))}（${lemma.length} 个字母）`
  if (level === 1) return shape
  return phonetic ? `${shape} ｜ /${phonetic}/` : shape
}
