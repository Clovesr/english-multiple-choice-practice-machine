import { describe, expect, it } from 'vitest'
import { pickVoice, summarizeCapability } from './speech'

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
  it('无本地英语时才用远程语音', () => {
    const remoteOnly = [
      { name: 'Remote US', lang: 'en-US', localService: false },
      { name: 'Local ZH', lang: 'zh-CN', localService: true },
    ]
    expect(pickVoice(remoteOnly, 'us')?.name).toBe('Remote US')
  })
  it('无英语语音返回 null', () => {
    expect(pickVoice([{ name: 'ZH', lang: 'zh-CN', localService: true }], 'us')).toBeNull()
  })
  it('lang 大小写与下划线变体可匹配', () => {
    expect(pickVoice([{ name: 'X', lang: 'en_US', localService: true }], 'us')?.name).toBe('X')
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
  it('纯本地英语 → localOnly=true 无警示', () => {
    const cap = summarizeCapability([{ name: 'L', lang: 'en-GB', localService: true }])
    expect(cap.localOnly).toBe(true)
    expect(cap.reason).toBe('')
  })
})
