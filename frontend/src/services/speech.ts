// 发音能力（API_CONTRACT §7.4）：优先浏览器 SpeechSynthesis 本地语音；
// 检测可用 en voice，无本地 voice 时上报能力状态（后端 Provider 兜底由 Codex 评估）。

export type Accent = 'us' | 'uk'

export interface SpeechCapability {
  available: boolean
  localOnly: boolean          // 是否全部为本地语音（离线可用的判断依据）
  voices: Array<{ name: string, lang: string, local: boolean }>
  reason: string              // 不可用/降级原因，用于设置页展示
}

/** 从 voice 列表挑选最合适的英语语音（纯逻辑，可单测）。 */
export function pickVoice<T extends { lang: string, localService?: boolean, name?: string }>(
  voices: T[],
  accent: Accent,
): T | null {
  const wanted = accent === 'uk' ? 'en-gb' : 'en-us'
  const lang = (v: T) => v.lang.toLowerCase().replace('_', '-')
  const isLocal = (v: T) => v.localService !== false
  // 优先级：本地+精确口音 > 本地+任意英语 > 远程+精确口音 > 远程+任意英语
  return (
    voices.find((v) => isLocal(v) && lang(v) === wanted)
    ?? voices.find((v) => isLocal(v) && lang(v).startsWith('en'))
    ?? voices.find((v) => lang(v) === wanted)
    ?? voices.find((v) => lang(v).startsWith('en'))
    ?? null
  )
}

/** 汇总当前发音能力（设置页与听音卡降级提示都用它）。 */
export function summarizeCapability(
  voices: Array<{ lang: string, localService?: boolean, name?: string }>,
): SpeechCapability {
  const english = voices.filter((v) => v.lang.toLowerCase().replace('_', '-').startsWith('en'))
  if (!english.length) {
    return { available: false, localOnly: false, voices: [], reason: '系统没有可用的英语语音，听音卡将降级为可跳过。' }
  }
  const mapped = english.map((v) => ({ name: v.name ?? '', lang: v.lang, local: v.localService !== false }))
  const localOnly = mapped.every((v) => v.local)
  return {
    available: true,
    localOnly,
    voices: mapped,
    reason: localOnly ? '' : '部分语音需要联网，断网时可能不可用。',
  }
}

let cachedVoices: SpeechSynthesisVoice[] | null = null

/** 读取浏览器语音列表（Chrome 首次调用可能为空，监听 voiceschanged 一次）。 */
export function loadVoices(timeoutMs = 1500): Promise<SpeechSynthesisVoice[]> {
  if (typeof speechSynthesis === 'undefined') return Promise.resolve([])
  const now = speechSynthesis.getVoices()
  if (now.length) { cachedVoices = now; return Promise.resolve(now) }
  return new Promise((resolve) => {
    const finish = () => {
      cachedVoices = speechSynthesis.getVoices()
      resolve(cachedVoices)
    }
    const timer = setTimeout(finish, timeoutMs)
    speechSynthesis.addEventListener('voiceschanged', () => { clearTimeout(timer); finish() }, { once: true })
  })
}

export async function getCapability(): Promise<SpeechCapability> {
  const voices = await loadVoices()
  return summarizeCapability(voices)
}

/** 朗读文本；返回是否成功启动（不等待读完）。 */
export async function speak(text: string, accent: Accent = 'us', rate = 1): Promise<boolean> {
  if (typeof speechSynthesis === 'undefined' || !text.trim()) return false
  const voices = cachedVoices ?? await loadVoices()
  const voice = pickVoice(voices, accent)
  if (!voice) return false
  speechSynthesis.cancel()
  const utterance = new SpeechSynthesisUtterance(text)
  utterance.voice = voice
  utterance.lang = voice.lang
  utterance.rate = rate
  speechSynthesis.speak(utterance)
  return true
}
