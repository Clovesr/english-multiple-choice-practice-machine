import { describe, expect, it } from 'vitest'
import { buildHint, highlightSegments, insertDrill, isObjectiveCard, isRecallReverse, judgeLocally, newAttemptId, normalizeAnswer, pickCardForWord, ratingFromKey, sortReviewsFirst, suggestedRating } from './study'

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

describe('highlightSegments', () => {
  it('高亮原形与词形变化（大小写不敏感、词边界）', () => {
    const segs = highlightSegments('Her abilities exceed his Ability.', ['ability', 'abilities'])
    expect(segs.filter(s => s.hit).map(s => s.text)).toEqual(['abilities', 'Ability'])
    expect(segs.map(s => s.text).join('')).toBe('Her abilities exceed his Ability.')
  })
  it('不误伤子串（scale 不命中 scales 之外的 escalate）', () => {
    const segs = highlightSegments('They escalate as the scale grows.', ['scale'])
    expect(segs.filter(s => s.hit).map(s => s.text)).toEqual(['scale'])
  })
  it('空目标返回整句', () => {
    expect(highlightSegments('Hello world.', [])).toEqual([{ text: 'Hello world.', hit: false }])
  })
})

describe('buildHint', () => {
  it('一级提示：首字母+长度', () => {
    expect(buildHint(1, 'ability', "ә'biliti")).toBe('a······（7 个字母）')
  })
  it('二级提示追加音标；无音标时保持一级', () => {
    expect(buildHint(2, 'ability', "ә'biliti")).toContain("/ә'biliti/")
    expect(buildHint(2, 'cat', '')).toBe('c··（3 个字母）')
  })
  it('零级无提示', () => {
    expect(buildHint(0, 'ability', 'x')).toBeNull()
  })
})

describe('sortReviewsFirst', () => {
  it('复习卡排在新卡前，组内相对顺序保持', () => {
    const make = (id: number, state: string) => ({ card_id: id, state } as any)
    const sorted = sortReviewsFirst([make(1, 'new'), make(2, 'review'), make(3, 'new'), make(4, 'learning')])
    expect(sorted.map(c => c.card_id)).toEqual([2, 4, 1, 3])
  })
})

describe('insertDrill', () => {
  const item = (id: number, drillCount = 0, drill = false) => ({ card: { card_id: id } as any, drill, drillCount })
  it('副本插到 gap 张之后并计数', () => {
    const queue = [item(1), item(2), item(3), item(4), item(5), item(6)]
    const next = insertDrill(queue, item(9), 4)
    expect(next.map(q => q.card.card_id)).toEqual([1, 2, 3, 4, 9, 5, 6])
    expect(next[4].drill).toBe(true)
    expect(next[4].drillCount).toBe(1)
  })
  it('队列不足 gap 时插到队尾', () => {
    const next = insertDrill([item(1)], item(9), 4)
    expect(next.map(q => q.card.card_id)).toEqual([1, 9])
  })
  it('达到重练上限不再插入', () => {
    const queue = [item(1)]
    expect(insertDrill(queue, item(9, 2), 4, 2)).toBe(queue)
  })
})

describe('pickCardForWord（本体先行）', () => {
  const make = (id: number, type: string, state = 'new') => ({ card_id: id, card_type: type, state } as any)
  it('全新词优先取认词卡', () => {
    expect(pickCardForWord([make(1, 'spelling'), make(2, 'forward'), make(3, 'listening')]).card_id).toBe(2)
  })
  it('无认词卡时按 回忆>听音>拼写 顺位', () => {
    expect(pickCardForWord([make(1, 'cloze'), make(2, 'listening')]).card_id).toBe(2)
  })
  it('有到期复习卡则复习优先', () => {
    expect(pickCardForWord([make(1, 'forward'), make(2, 'spelling', 'review')]).card_id).toBe(2)
  })
})
