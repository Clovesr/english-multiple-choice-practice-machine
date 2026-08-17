<script setup lang="ts">
import { GraduationCap, Loader2, PartyPopper } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import type { ApiError } from '../api'
import {
  type Rating,
  type StudyCard,
  type StudySession,
  gradeStudyCard,
  getStudySession,
  newAttemptId,
  suspendStudyCard,
} from '../services/study'
import { getCapability } from '../services/speech'
import StudyCardView from '../components/study/StudyCardView.vue'

const session = ref<StudySession | null>(null)
const queue = ref<StudyCard[]>([])
const loading = ref(true)
const backendReady = ref(true)
const loadError = ref('')
const speechAvailable = ref(false)
const doneCount = ref(0)
const grading = ref(false)

const current = computed(() => queue.value[0] ?? null)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    session.value = await getStudySession(20)
    queue.value = [...session.value.cards]
  } catch (cause) {
    const error = cause as ApiError
    if (error.status === 404 || error.status === 405) backendReady.value = false
    else loadError.value = error.message || '学习会话加载失败'
  } finally {
    loading.value = false
  }
}

async function onGrade(payload: { rating: Rating, answer_given?: string, duration_ms: number }) {
  if (!current.value || grading.value) return
  grading.value = true
  const card = current.value
  try {
    await gradeStudyCard(card.card_id, { attempt_id: newAttemptId(), ...payload })
    queue.value = queue.value.slice(1)
    doneCount.value += 1
    if (session.value) session.value.counts.done_today += 1
  } catch (cause) {
    loadError.value = `评分保存失败：${(cause as Error).message}。这张卡留在队列里，可重试。`
  } finally {
    grading.value = false
  }
}

async function onSkip() {
  if (!current.value) return
  const card = current.value
  try {
    await suspendStudyCard(card.card_id)
    queue.value = queue.value.slice(1)
  } catch {
    // 暂停失败就只是移到队尾，不打断学习
    queue.value = [...queue.value.slice(1), card]
  }
}

onMounted(async () => {
  speechAvailable.value = (await getCapability()).available
  await load()
})
</script>

<template>
  <div class="page" style="max-width:860px">
    <div class="page-head">
      <div>
        <span class="eyebrow">STUDY SESSION</span>
        <h1>词汇学习</h1>
        <p class="lead" v-if="session">
          待复习 {{ session.counts.due_remaining }} · 新卡 {{ session.counts.new_remaining }} · 今日已完成 {{ session.counts.done_today }}
        </p>
      </div>
      <RouterLink class="button secondary compact" to="/wordbooks"><GraduationCap :size="16" />词书与计划</RouterLink>
    </div>

    <div v-if="loading" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在准备学习队列…</p></div>

    <div v-else-if="!backendReady" class="card empty">
      <strong>学习会话接口还在开发中</strong>
      <p>后端 /api/study/session 尚未上线（Codex Issue #2 进行中）。上线后这里会出现你的每日新词与到期复习队列。</p>
      <p style="font-size:12px">在此之前可以先去资源库阅读并收藏生词。</p>
      <RouterLink class="button secondary" to="/resources">去资源库</RouterLink>
    </div>

    <div v-else-if="loadError" class="warning">
      {{ loadError }}
      <button class="button ghost compact" type="button" @click="load">重试</button>
    </div>

    <template v-else-if="current">
      <StudyCardView :card="current" :speech-available="speechAvailable" @grade="onGrade" @skip="onSkip" />
    </template>

    <div v-else class="card empty">
      <PartyPopper :size="26" style="color:var(--primary)" />
      <strong v-if="doneCount">今天的队列清完了，共 {{ doneCount }} 张</strong>
      <strong v-else>当前没有到期的卡片</strong>
      <p>想学更多，去词书页调整每日新词量，或在资源库阅读时收藏新词。</p>
      <div style="display:flex;gap:10px;justify-content:center">
        <RouterLink class="button secondary compact" to="/wordbooks">词书与计划</RouterLink>
        <RouterLink class="button ghost compact" to="/resources">去阅读</RouterLink>
      </div>
    </div>
  </div>
</template>
