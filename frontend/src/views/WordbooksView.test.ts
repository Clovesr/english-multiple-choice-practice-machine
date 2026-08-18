// @vitest-environment jsdom
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

// VOC-27 前端面回归（034 §2.1）：词表导入成功报告与失败错误态
const apiMock = vi.fn()
vi.mock('../api', () => ({
  api: (...args: unknown[]) => apiMock(...args),
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  del: vi.fn(),
}))

const listWordbooksMock = vi.fn()
vi.mock('../services/study', () => ({
  listWordbooks: () => listWordbooksMock(),
  activateWordbookPlan: vi.fn(),
  deactivateWordbookPlan: vi.fn(),
}))

import WordbooksView from './WordbooksView.vue'

const mountOptions = {
  global: {
    stubs: {
      RouterLink: { template: '<a><slot /></a>' },
      VocabTabs: true,
    },
  },
}

function pickFile(wrapper: ReturnType<typeof mount>) {
  const input = wrapper.find('input[type="file"]')
  const file = new File(['alpha\nbeta\n'], 'list.txt', { type: 'text/plain' })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  return input.trigger('change')
}

describe('WordbooksView 词表导入（VOC-27）', () => {
  beforeEach(() => {
    apiMock.mockReset()
    listWordbooksMock.mockReset()
    listWordbooksMock.mockResolvedValue({ items: [] })
  })

  it('导入成功：显示 matched/unmatched 报告并刷新列表', async () => {
    apiMock.mockResolvedValue({
      wordbook: { name: '我的词表' },
      matched: 5,
      unmatched: [{ term: 'foobar' }],
    })
    const wrapper = mount(WordbooksView, mountOptions)
    await flushPromises()
    await pickFile(wrapper)
    await flushPromises()

    expect(apiMock).toHaveBeenCalledWith('/wordbooks/import', expect.objectContaining({ method: 'POST' }))
    expect(wrapper.text()).toContain('「我的词表」导入完成：匹配词典 5 个')
    expect(wrapper.text()).toContain('未匹配 1 个')
    expect(wrapper.text()).toContain('foobar')
    expect(listWordbooksMock).toHaveBeenCalledTimes(2) // 挂载 + 导入后刷新
  })

  it('导入失败：错误提示包含原因，报告不渲染', async () => {
    apiMock.mockRejectedValue(new Error('词表文件为空'))
    const wrapper = mount(WordbooksView, mountOptions)
    await flushPromises()
    await pickFile(wrapper)
    await flushPromises()

    expect(wrapper.text()).toContain('词表导入失败：词表文件为空')
    expect(wrapper.text()).not.toContain('导入完成')
    expect(listWordbooksMock).toHaveBeenCalledTimes(1) // 失败不刷新
  })

  it('空词书列表的空态给出真实导入指引（不再是"随后端上线"）', async () => {
    const wrapper = mount(WordbooksView, mountOptions)
    await flushPromises()
    expect(wrapper.text()).toContain('导入词表（TXT/CSV）')
    expect(wrapper.text()).not.toContain('功能随后端上线')
  })
})
