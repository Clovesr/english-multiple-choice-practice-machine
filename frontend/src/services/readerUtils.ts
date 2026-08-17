// 阅读器纯逻辑，独立成模块以便单测（KI-4）。

export interface SentenceContext {
  sentence: string
  before: string
  after: string
}

// 英文句界要求标点后有空白（避免误切 e.g. / 小数）；中文句界不要求空白。
const SENTENCE_BOUNDARY = /[.!?]["'”’)]?\s+|[。！？]["'”’)]?\s*|\n/g

/**
 * 在段落文本中定位选区所在句子，并给出前后各一句的语境。
 * offset 为选中词在 text 中的起始下标；找不到边界时整段作为句子。
 */
export function extractSentenceContext(text: string, offset: number, length: number): SentenceContext {
  const clamped = Math.max(0, Math.min(offset, text.length))
  const boundaries: number[] = [0]
  for (const match of text.matchAll(SENTENCE_BOUNDARY)) {
    boundaries.push(match.index! + match[0].length)
  }
  boundaries.push(text.length)

  let start = 0
  let end = text.length
  for (let i = 0; i < boundaries.length - 1; i++) {
    if (clamped >= boundaries[i] && clamped < boundaries[i + 1]) {
      start = boundaries[i]
      end = boundaries[i + 1]
      // 选区跨句时向后扩到选区结束所在句
      while (end < text.length && clamped + length > end) {
        const next = boundaries.find((b) => b > end)
        end = next ?? text.length
      }
      break
    }
  }
  const beforeStart = boundaries.filter((b) => b < start).pop() ?? 0
  const afterEnd = boundaries.find((b) => b > end) ?? text.length
  return {
    sentence: text.slice(start, end).trim(),
    before: text.slice(beforeStart, start).trim(),
    after: text.slice(end, afterEnd).trim(),
  }
}

/** 清洗选区文本为可收藏的词/短语；超长或空白返回 null。 */
export function normalizeSelection(raw: string): string | null {
  const text = raw.replace(/\s+/g, ' ').trim()
  if (!text) return null
  if (text.length > 80) return null
  if (!/[A-Za-z]/.test(text)) return null
  return text
}

/** 由滚动位置计算 0~1 的进度比。容器不可滚动时返回 1（内容全部可见）。 */
export function scrollRatio(scrollTop: number, scrollHeight: number, clientHeight: number): number {
  const range = scrollHeight - clientHeight
  if (range <= 0) return 1
  return Math.min(1, Math.max(0, scrollTop / range))
}

/** 找到当前视口顶端附近的段落元素 id（data-segment-id 已排序传入）。 */
export function nearestSegmentId(tops: Array<{ id: number, top: number }>, viewportTop: number): number | null {
  let candidate: number | null = null
  for (const { id, top } of tops) {
    if (top <= viewportTop + 8) candidate = id
    else break
  }
  return candidate ?? (tops.length ? tops[0].id : null)
}
