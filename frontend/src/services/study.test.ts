import { describe, expect, it } from 'vitest'
import { isObjectiveCard, judgeLocally, newAttemptId, normalizeAnswer, ratingFromKey, suggestedRating } from './study'
import { pickVoice, summarizeCapability } from './speech'

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

describe('pickVoice', () => {
  const voices = [
    { name: 'Remote US', lang: 'en-US', localService: false },
    { name: 'Local UK', lang: 'en-GB', localService: true },
    { name: 'Local ZH', lang: 'zh-CN', localService: true },
  ]
  it('优先本地精确口音', () => {
    expect(pickVoice(voices, 'uk')?.name).toBe('Local UK')
  })
  it('无本地精确口音时退到本地任意英语', () => {
    expect(pickVoice(voices, 'us')?.name).toBe('Local UK')
  })
  it('无英语语音返回 null', () => {
    expect(pickVoice([{ name: 'ZH', lang: 'zh-CN', localService: true }], 'us')).toBeNull()
  })
})

describe('summarizeCapability', () => {
  it('无英语语音 → 不可用并给原因', () => {
    const cap = summarizeCapability([{ name: 'ZH', lang: 'zh-CN', localService: true }])
    expect(cap.available).toBe(false)
    expect(cap.reason).toContain('听音卡')
  })
  it('含远程语音 → localOnly=false 并提示断网风险', () => {
    const cap = summarizeCapability([
      { name: 'L', lang: 'en-US', localService: true },
      { name: 'R', lang: 'en-GB', localService: false },
    ])
    expect(cap.available).toBe(true)
    expect(cap.localOnly).toBe(false)
    expect(cap.reason).toContain('联网')
  })
})
