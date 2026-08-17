<script setup lang="ts">
import { Loader2, PartyPopper, Settings2 } from 'lucide-vue-next'
import { computed, inject, onMounted, ref } from 'vue'
import type { Ref } from 'vue'
import type { ApiError } from '../api'
import {
  type CardType,
  type QueueItem,
  type Rating,
  type StudyCard,
  type StudyOverview,
  type StudySession,
  type StudySettings,
  getStudySession,
  getStudySettings,
  gradeStudyCard,
  insertDrill,
  newAttemptId,
  pickCardForWord,
  putStudySettings,
  sortReviewsFirst,
  suspendStudyCard,
} from '../services/study'
import { getCapability } from '../services/speech'
import StudyCardView from '../components/study/StudyCardView.vue'

// 模块外壳提供的常驻数据条（评分后刷新它）
const moduleOverview = inject<{ overview: Ref<StudyOverview | null>, refresh: () => Promise<void> } | null>('vocabOverview', null)

const session = ref<StudySession | null>(null)
const settings = ref<StudySettings | null>(null)
const queue = ref<QueueItem[]>([])
const sessionDone = ref(0)
const sessionPlanned = ref(0)
const completedToday = ref<StudyCard[]>([])
const consolidating = ref(false)
const loading = ref(true)
const backendReady = ref(true)
const loadError = ref('')
const speechAvailable = ref(false)
const doneCount = ref(0)
const grading = ref(false)
const showSettings = ref(false)
const savingSettings = ref(false)
const lastFetchHadCards = ref(true)

const current = computed(() => queue.value[0] ?? null)

// 同词去重（Anki bury siblings 的会话级过渡实现，词级调度重构由后端进行中）：
// 本次学习会话中每个词只出现一次；已评分词的其余题型卡不再进入本次会话。
const encounteredLemmas = new Set<string>()

function dedupeByWord(cards: StudyCard[]): StudyCard[] {
  const byLemma = new Map<string, StudyCard[]>()
  for (const card of cards) {
    const key = card.entry.lemma.toLowerCase()
    if (encounteredLemmas.has(key)) continue
    const bucket = byLemma.get(key)
    if (bucket) bucket.push(card)
    else byLemma.set(key, [card])
  }
  // 同词多卡：到期复习优先；全新词取"本体先行"优先级（认词>回忆>听音>拼写>挖空>搭配）
  return [...byLemma.values()].map(pickCardForWord)
}

function toQueue(cards: StudyCard[]): QueueItem[] {
  // 先复习后新学（百词斩两阶段），再包装为队列项
  return sortReviewsFirst(dedupeByWord(cards)).map((card) => ({ card, drill: false, drillCount: 0 }))
}

const cardTypeLabels: Record<CardType, string> = {
  forward: '认词', reverse: '辨义', listening: '听音', spelling: '拼写', cloze: '挖空', collocation: '搭配',
}

async function loadOverview() {
  await moduleOverview?.refresh()
}

async function load(refetch = false) {
  loading.value = !refetch
  loadError.value = ''
  try {
    session.value = await getStudySession(20)
    lastFetchHadCards.value = session.value.cards.length > 0
    queue.value = toQueue(session.value.cards)
    sessionPlanned.value = sessionDone.value + queue.value.length
    // 整批都是本会话已见过的词 → 视为清空，避免无限补拉
    if (!queue.value.length && session.value.cards.length) lastFetchHadCards.value = false
    void loadOverview()
  } catch (cause) {
    const error = cause as ApiError
    if (error.status === 404 || error.status === 405 || error.code === 'endpoint_missing') backendReady.value = false
    else loadError.value = error.message || '学习会话加载失败'
  } finally {
    loading.value = false
  }
}

async function loadSettings() {
  try {
    settings.value = await getStudySettings()
  } catch (cause) {
    loadError.value = (cause as Error).message
  }
}

async function toggleSettings() {
  showSettings.value = !showSettings.value
  if (showSettings.value && !settings.value) await loadSettings()
}

async function saveSettings() {
  if (!settings.value) return
  savingSettings.value = true
  try {
    settings.value = await putStudySettings(settings.value)
    showSettings.value = false
    await load(true) // 上限变化影响队列口径
  } catch (cause) {
    loadError.value = `设置保存失败：${(cause as Error).message}`
  } finally {
    savingSettings.value = false
  }
}

function toggleCardType(type: CardType) {
  if (!settings.value) return
  const list = settings.value.enabled_card_types
  settings.value.enabled_card_types = list.includes(type)
    ? list.filter((item) => item !== type)
    : [...list, type]
}

async function onGrade(payload: { rating: Rating, answer_given?: string, duration_ms: number }) {
  if (!current.value || grading.value) return
  grading.value = true
  const item = current.value
  try {
    if (item.drill) {
      // 重练副本：当天首次评分已写入调度，这里只做本地清障（墨墨：非首次照面不影响长期排期）
      queue.value = payload.rating <= 2
        ? insertDrill(queue.value.slice(1), item)
        : queue.value.slice(1)
    } else {
      await gradeStudyCard(item.card.card_id, { attempt_id: newAttemptId(), ...payload })
      encounteredLemmas.add(item.card.entry.lemma.toLowerCase())
      completedToday.value = [...completedToday.value, item.card]
      let next = queue.value.slice(1)
      // 记错/模糊的词：几张卡后当日重练，直到过关
      if (payload.rating <= 2) next = insertDrill(next, item)
      queue.value = next
      doneCount.value += 1
      sessionDone.value += 1
      if (session.value) session.value.counts.done_today += 1
    }
    if (!queue.value.length && lastFetchHadCards.value) await load(true) // 增量补池
    else if (doneCount.value % 5 === 0) void loadOverview()
  } catch (cause) {
    const error = cause as ApiError
    if (error.status === 409) {
      // 卡片状态在批次创建后已变（如被暂停）：移出继续，不阻塞学习流
      encounteredLemmas.add(item.card.entry.lemma.toLowerCase())
      queue.value = queue.value.slice(1)
      if (!queue.value.length && lastFetchHadCards.value) await load(true)
    } else {
      loadError.value = `评分保存失败：${error.message}。这张卡留在队列里，可重试。`
    }
  } finally {
    grading.value = false
  }
}

async function onSkip() {
  if (!current.value) return
  const item = current.value
  if (item.drill) { queue.value = queue.value.slice(1); return }
  try {
    await suspendStudyCard(item.card.card_id)
    encounteredLemmas.add(item.card.entry.lemma.toLowerCase())
    queue.value = queue.value.slice(1)
    if (!queue.value.length && lastFetchHadCards.value) await load(true)
  } catch {
    queue.value = [...queue.value.slice(1), item]
  }
}

/** 巩固今日所学：本会话学过的词以测验形态再过一遍（本地巩固，不写长期排期）。 */
function startConsolidation() {
  if (!completedToday.value.length) return
  consolidating.value = true
  const shuffled = [...completedToday.value].sort(() => Math.random() - 0.5)
  queue.value = shuffled.map((card) => ({ card, drill: true, drillCount: 0 }))
  lastFetchHadCards.value = false // 巩固结束后不自动拉新批次
}

onMounted(async () => {
  speechAvailable.value = (await getCapability()).available
  await load()
})
</script>

<template>
  <div class="vocab-pane" style="max-width:760px;margin:0 auto">
    <div class="vocab-pane-toolbar">
      <p class="lead" v-if="session" style="margin:0">
        待复习 {{ session.counts.due_remaining }} · 新卡 {{ session.counts.new_remaining }} · 今日已完成 {{ session.counts.done_today }}
      </p>
      <span v-else style="flex:1" />
      <button class="button ghost compact" type="button" aria-label="学习设置" @click="toggleSettings"><Settings2 :size="16" />学习设置</button>
    </div>

    <section v-if="showSettings && settings" class="card" style="margin-bottom:16px;display:grid;gap:14px">
      <strong>学习设置</strong>
      <div style="display:flex;gap:16px;flex-wrap:wrap">
        <div class="field" style="margin:0"><label>每日新卡上限</label>
          <input v-model.number="settings.daily_new" type="number" min="0" max="500" style="width:110px" /></div>
        <div class="field" style="margin:0"><label>每日复习上限</label>
          <input v-model.number="settings.daily_review_max" type="number" min="0" max="2000" style="width:110px" /></div>
      </div>
      <div>
        <label style="font-size:13px;color:var(--muted)">启用的卡片类型</label>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px">
          <button
            v-for="(label, type) in cardTypeLabels" :key="type" type="button" class="pill"
            :style="settings.enabled_card_types.includes(type as CardType) ? 'outline:2px solid var(--primary)' : 'opacity:.5'"
            @click="toggleCardType(type as CardType)"
          >{{ label }}</button>
        </div>
      </div>
      <div style="display:flex;gap:10px">
        <button class="button compact" type="button" :disabled="savingSettings" @click="saveSettings">
          <Loader2 v-if="savingSettings" :size="14" class="spinning" /><span v-else>保存并刷新队列</span>
        </button>
        <button class="button ghost compact" type="button" @click="showSettings = false">收起</button>
      </div>
    </section>

    <div v-if="loading" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在准备学习队列…</p></div>

    <div v-else-if="!backendReady" class="card empty">
      <strong>学习会话接口不可用</strong>
      <p>当前后端版本没有 /api/study/session。请确认应用已更新到含学习内核的版本后重试。</p>
      <RouterLink class="button secondary" to="/resources">先去资源库</RouterLink>
    </div>

    <div v-else-if="loadError" class="warning">
      {{ loadError }}
      <button class="button ghost compact" type="button" @click="load()">重试</button>
    </div>

    <template v-else-if="current">
      <div class="session-progress" aria-hidden="true">
        <div class="session-progress-bar"><div :style="`width:${sessionPlanned ? Math.min(100, Math.round(sessionDone / sessionPlanned * 100)) : 0}%`" /></div>
        <small v-if="consolidating">巩固模式 · 剩余 {{ queue.length }}</small><small v-else>{{ sessionDone }} / {{ sessionPlanned }}<template v-if="current.drill"> · 巩固不计入</template></small>
      </div>
      <StudyCardView :card="current.card" :drill="current.drill" :speech-available="speechAvailable" @grade="onGrade" @skip="onSkip" />
    </template>

<style scoped>
.session-progress { max-width: 680px; margin: 0 auto 10px; display: flex; align-items: center; gap: 12px; }
.session-progress-bar { flex: 1; height: 5px; border-radius: 999px; background: var(--line); overflow: hidden; }
.session-progress-bar div { height: 100%; background: var(--primary); transition: width .25s ease; }
.session-progress small { color: var(--muted); font-size: 12px; white-space: nowrap; }
</style>

    <div v-else class="card empty">
      <PartyPopper :size="26" style="color:var(--primary)" />
      <strong v-if="consolidating">巩固完成，今天的 {{ completedToday.length }} 个词都过了第二遍</strong>
      <strong v-else-if="doneCount">今天的队列清完了，共 {{ doneCount }} 张</strong>
      <strong v-else>当前没有到期的卡片</strong>
      <p v-if="!consolidating && completedToday.length">趁热打铁：把今天学过的 {{ completedToday.length }} 个词用测验再过一遍，不影响记忆排期。</p>
      <p v-else>想学更多，去词书页调整每日新词量，或在资源库阅读时收藏新词。</p>
      <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap">
        <button v-if="completedToday.length && !consolidating" class="button compact" type="button" @click="startConsolidation">巩固今日所学（{{ completedToday.length }} 词）</button>
        <button v-else-if="consolidating && completedToday.length" class="button secondary compact" type="button" @click="startConsolidation">再巩固一轮</button>
        <RouterLink class="button secondary compact" to="/wordbooks">词书与计划</RouterLink>
        <RouterLink class="button ghost compact" to="/resources">去阅读</RouterLink>
      </div>
    </div>
  </div>
</template>

