// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import type { StudyCard } from '../../services/study'
import StudyCardView from './StudyCardView.vue'
import RatingBar from './RatingBar.vue'
import OptionList from './OptionList.vue'

vi.mock('../../services/speech', () => ({ speak: vi.fn().mockResolvedValue(true) }))

function makeCard(overrides: Partial<StudyCard> = {}): StudyCard {
  return {
    card_id: 1,
    review_item_id: 1,
    card_type: 'forward',
    state: 'review',
    entry: {
      lemma: 'resilience',
      phonetic_uk: 'rɪˈzɪlɪəns',
      phonetic_us: 'rɪˈzɪliəns',
      senses: [{ pos: 'n.', gloss_zh: '恢复力；韧性' }],
    },
    prompt: {},
    answer: {},
    contexts: [{ sentence: 'Her resilience surprised everyone.', source: 'Text 1' }],
    ...overrides,
  }
}

const mountCard = (card: StudyCard) => mount(StudyCardView, { props: { card, speechAvailable: true } })

describe('正向卡（reveal 流）', () => {
  it('先隐藏答案，空格/按钮翻面后可评分', async () => {
    const wrapper = mountCard(makeCard())
    expect(wrapper.text()).toContain('resilience')
    expect(wrapper.text()).not.toContain('恢复力')
    await wrapper.find('.study-answer-zone button').trigger('click')
    expect(wrapper.text()).toContain('恢复力')
    wrapper.findComponent(RatingBar).vm.$emit('rate', 3)
    const events = wrapper.emitted('grade')
    expect(events).toHaveLength(1)
    expect(events![0][0]).toMatchObject({ rating: 3 })
  })
})

describe('拼写卡（typing 流）', () => {
  const card = makeCard({
    card_type: 'spelling',
    prompt: { text: 'n. 恢复力；韧性' },
    answer: { text: 'resilience', accept: ['resilience'] },
  })

  it('答对：即时判定 + 建议评分 Good，grade 带 answer_given', async () => {
    const wrapper = mountCard(card)
    await wrapper.find('input').setValue(' Resilience ')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.spelling-input').classes()).toContain('ok')
    expect(wrapper.findComponent(RatingBar).props('suggested')).toBe(3)
    wrapper.findComponent(RatingBar).vm.$emit('rate', 3)
    expect(wrapper.emitted('grade')![0][0]).toMatchObject({ rating: 3, answer_given: ' Resilience ' })
  })

  it('答错：标红、显示正确答案、建议 Again', async () => {
    const wrapper = mountCard(card)
    await wrapper.find('input').setValue('resiliance')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.spelling-input').classes()).toContain('bad')
    expect(wrapper.text()).toContain('正确答案：resilience')
    expect(wrapper.findComponent(RatingBar).props('suggested')).toBe(1)
  })
})

describe('反向卡（choice 流）', () => {
  it('选项含正确答案与干扰项，选错后揭示正确项', async () => {
    const wrapper = mountCard(makeCard({
      card_type: 'reverse',
      prompt: { text: 'n. 恢复力；韧性' },
      answer: { text: 'resilience', distractors: ['routine', 'committee', 'scale'] },
    }))
    const list = wrapper.findComponent(OptionList)
    const labels = list.findAll('button').map((b) => b.text())
    expect(labels).toHaveLength(4)
    expect(labels).toContain('resilience')
    const wrong = list.findAll('button').find((b) => b.text() === 'routine')!
    await wrong.trigger('click')
    expect(wrong.classes()).toContain('wrong')
    const correct = list.findAll('button').find((b) => b.text() === 'resilience')!
    expect(correct.classes()).toContain('reveal')
    expect(wrapper.findComponent(RatingBar).props('suggested')).toBe(1)
  })
})

describe('听音卡降级（十条之 9 / A10.4）', () => {
  it('无语音能力时给出降级说明且可跳过', async () => {
    const wrapper = mount(StudyCardView, {
      props: { card: makeCard({ card_type: 'listening', prompt: { tts_text: 'resilience' }, answer: { accept: ['resilience'] } }), speechAvailable: false },
    })
    expect(wrapper.text()).toContain('没有可用的英语语音')
    await wrapper.find('.study-foot button').trigger('click')
    expect(wrapper.emitted('skip')).toHaveLength(1)
  })
})

describe('挖空卡', () => {
  it('句子含空位，翻面后空位处显示答案', async () => {
    const wrapper = mountCard(makeCard({
      card_type: 'cloze',
      prompt: { cloze_sentence: 'Her ______ surprised everyone.' },
      answer: { text: 'resilience', accept: ['resilience'] },
    }))
    expect(wrapper.find('.cloze-blank').text()).toBe('______')
    await wrapper.find('input').setValue('resilience')
    await wrapper.find('form').trigger('submit')
    expect(wrapper.find('.cloze-blank').text()).toBe('resilience')
  })
})

describe('新词首照面教学模式（任何题型）', () => {
  it('新的拼写卡不出输入框，直接展示本体与三键', async () => {
    const wrapper = mountCard(makeCard({
      card_type: 'spelling',
      state: 'new',
      prompt: { text: 'n. 恢复力' },
      answer: { text: 'resilience', accept: ['resilience'] },
    }))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.spelling-input').exists()).toBe(false)
    expect(wrapper.find('.study-word').text()).toBe('resilience')
    expect(wrapper.text()).toContain('恢复力')
    const labels = wrapper.findAll('.rating-bar button').map(b => b.text().replace(/\d/g, '').trim())
    expect(labels).toEqual(['有点难', '记住了', '太简单，斩'])
  })
  it('重练副本恢复为正常测验形态', () => {
    const wrapper = mount(StudyCardView, {
      props: {
        card: makeCard({ card_type: 'spelling', state: 'new', prompt: { text: 'n. 恢复力' }, answer: { accept: ['resilience'] } }),
        speechAvailable: true,
        drill: true,
      },
    })
    expect(wrapper.find('.spelling-input').exists()).toBe(true)
  })
})
