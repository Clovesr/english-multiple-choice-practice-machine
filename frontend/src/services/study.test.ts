import { describe, expect, it } from 'vitest'
import { isObjectiveCard, isRecallReverse, judgeLocally, newAttemptId, normalizeAnswer, ratingFromKey, suggestedRating } from './study'

describe('normalizeAnswer', () => {
  it('小写、去空白、压缩空格、NFKC', () => {
    expect(normalizeAnswer('  Colour ')).toBe('colour')
    expect(normalizeAnswer('give   up')).toBe('give up')
    expect(normalizeAnswer('ｃａｆｅ')).toBe('cafe') // 全角→半角
  })
})

describe('judgeLocally', () => {
  const accept = ['color', 'colour']
  it('命中 accept（大小写/空白容错）', () => {
    expect(judgeLocally(' COLOUR ', accept)).toBe(true)
    expect(judgeLocally('color', accept)).toBe(true)
  })
  it('未命中为 false，空输入为 false', () => {
    expect(judgeLocally('colr', accept)).toBe(false)
    expect(judgeLocally('   ', accept)).toBe(false)
  })
  it('无 accept 列表（主观卡）返回 null', () => {
    expect(judgeLocally('anything', undefined)).toBeNull()
    expect(judgeLocally('anything', [])).toBeNull()
  })
})

describe('suggestedRating / ratingFromKey / attempt_id', () => {
  it('错→1 对→3', () => {
    expect(suggestedRating(false)).toBe(1)
    expect(suggestedRating(true)).toBe(3)
  })
  it('数字键映射，其余为 null', () => {
    expect(ratingFromKey('1')).toBe(1)
    expect(ratingFromKey('4')).toBe(4)
    expect(ratingFromKey('5')).toBeNull()
    expect(ratingFromKey('a')).toBeNull()
  })
  it('attempt_id 每次唯一', () => {
    expect(newAttemptId()).not.toBe(newAttemptId())
  })
  it('attempt_id 是合法 RFC 4122 UUID（后端按 UUID 校验，含 fallback 路径）', () => {
    const pattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
    for (let i = 0; i < 20; i++) {
      expect(newAttemptId()).toMatch(pattern)
    }
    // 强制走无 randomUUID 的 fallback 分支
    const original = globalThis.crypto
    const stub = { getRandomValues: original.getRandomValues.bind(original) } as Crypto
    Object.defineProperty(globalThis, 'crypto', { value: stub, configurable: true })
    try {
      for (let i = 0; i < 20; i++) expect(newAttemptId()).toMatch(pattern)
    } finally {
      Object.defineProperty(globalThis, 'crypto', { value: original, configurable: true })
    }
  })
})

describe('isObjectiveCard', () => {
  it('forward 永远主观', () => {
    expect(isObjectiveCard({ card_type: 'forward', answer: { accept: ['x'] } })).toBe(false)
  })
  it('有 accept 或干扰项为客观', () => {
    expect(isObjectiveCard({ card_type: 'spelling', answer: { accept: ['x'] } })).toBe(true)
    expect(isObjectiveCard({ card_type: 'reverse', answer: { distractors: ['a', 'b'] } })).toBe(true)
    expect(isObjectiveCard({ card_type: 'reverse', answer: {} })).toBe(false)
  })
})

describe('isRecallReverse', () => {
  it('反向卡无干扰项 → 主动回忆（主观自评）', () => {
    expect(isRecallReverse({ card_type: 'reverse', answer: { accept: ['ability'], distractors: [] } })).toBe(true)
    expect(isRecallReverse({ card_type: 'reverse', answer: { accept: ['ability'] } })).toBe(true)
  })
  it('有干扰项或非反向卡 → false', () => {
    expect(isRecallReverse({ card_type: 'reverse', answer: { distractors: ['a'] } })).toBe(false)
    expect(isRecallReverse({ card_type: 'spelling', answer: {} })).toBe(false)
  })
})
