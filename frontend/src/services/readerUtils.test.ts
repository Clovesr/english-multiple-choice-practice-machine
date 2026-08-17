import { describe, expect, it } from 'vitest'
import { extractSentenceContext, nearestSegmentId, normalizeSelection, scrollRatio } from './readerUtils'

describe('extractSentenceContext', () => {
  const text = 'Habits are quiet. A habit is a decision you only make once. After that, the routine carries you.'

  it('定位选区所在句并给出前后语境', () => {
    const offset = text.indexOf('decision')
    const context = extractSentenceContext(text, offset, 'decision'.length)
    expect(context.sentence).toBe('A habit is a decision you only make once.')
    expect(context.before).toBe('Habits are quiet.')
    expect(context.after).toBe('After that, the routine carries you.')
  })

  it('首句无前文，尾句无后文', () => {
    const first = extractSentenceContext(text, 0, 6)
    expect(first.before).toBe('')
    expect(first.sentence).toBe('Habits are quiet.')
    const last = extractSentenceContext(text, text.indexOf('routine'), 7)
    expect(last.after).toBe('')
  })

  it('无句界时整段作为句子', () => {
    const context = extractSentenceContext('no boundary here', 3, 8)
    expect(context.sentence).toBe('no boundary here')
    expect(context.before).toBe('')
    expect(context.after).toBe('')
  })

  it('中文句号也是边界', () => {
    const zh = '第一句结束。Second sentence here. 第三句。'
    const context = extractSentenceContext(zh, zh.indexOf('Second'), 6)
    expect(context.sentence).toBe('Second sentence here.')
    expect(context.before).toBe('第一句结束。')
  })
})

describe('normalizeSelection', () => {
  it('清洗空白并保留短语', () => {
    expect(normalizeSelection('  spaced   repetition \n')).toBe('spaced repetition')
  })
  it('拒绝空、超长与纯非英文选择', () => {
    expect(normalizeSelection('   ')).toBeNull()
    expect(normalizeSelection('x'.repeat(81))).toBeNull()
    expect(normalizeSelection('。，！')).toBeNull()
  })
})

describe('scrollRatio', () => {
  it('普通滚动位置', () => {
    expect(scrollRatio(500, 2000, 1000)).toBe(0.5)
  })
  it('不可滚动时视为读完', () => {
    expect(scrollRatio(0, 800, 1000)).toBe(1)
  })
  it('钳制在 0~1', () => {
    expect(scrollRatio(-10, 2000, 1000)).toBe(0)
    expect(scrollRatio(9999, 2000, 1000)).toBe(1)
  })
})

describe('nearestSegmentId', () => {
  const tops = [
    { id: 11, top: -300 },
    { id: 12, top: -20 },
    { id: 13, top: 240 },
  ]
  it('取视口顶端上方最近的段落', () => {
    expect(nearestSegmentId(tops, 0)).toBe(12)
  })
  it('全部在下方时取第一段', () => {
    expect(nearestSegmentId([{ id: 5, top: 400 }], 0)).toBe(5)
  })
  it('空列表返回 null', () => {
    expect(nearestSegmentId([], 0)).toBeNull()
  })
})
