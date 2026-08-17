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
}

export interface StudyCard {
  card_id: number
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

/** 生成作答幂等键（契约 §1：attempt_id 为 UUID）。 */
export function newAttemptId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `${Date.now().toString(16)}-${Math.random().toString(16).slice(2, 10)}-${Math.random().toString(16).slice(2, 10)}`
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
