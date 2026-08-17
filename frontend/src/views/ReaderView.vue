<script setup lang="ts">
import { ArrowLeft, BookMarked, Loader2 } from 'lucide-vue-next'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import type { ApiError } from '../api'
import {
  type Resource,
  type ResourceSegment,
  collectWordFromSelection,
  getAllSegments,
  getResource,
  saveProgress,
} from '../services/resources'
import { extractSentenceContext, nearestSegmentId, normalizeSelection, scrollRatio } from '../services/readerUtils'

const route = useRoute()
const router = useRouter()
const resourceId = Number(route.params.id)

const resource = ref<Resource | null>(null)
const segments = ref<ResourceSegment[]>([])
const loading = ref(true)
const loadError = ref('')

const contentEl = ref<HTMLElement | null>(null)
const highlightSegment = ref<number | null>(null)

// 选词浮层
const popup = ref<{ x: number, y: number, term: string, segmentId: number, offset: number } | null>(null)
const collecting = ref(false)
const toast = ref('')
let toastTimer: ReturnType<typeof setTimeout> | undefined

// 进度保存
let pendingReadingMs = 0
let lastTick = 0
let saveTimer: ReturnType<typeof setInterval> | undefined
let scrollDebounce: ReturnType<typeof setTimeout> | undefined
let dirty = false

const isNeedsReview = computed(() => resource.value?.status === 'needs_review')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    resource.value = await getResource(resourceId)
    segments.value = resource.value.segment_count > 0 ? await getAllSegments(resourceId) : []
  } catch (cause) {
    loadError.value = (cause as Error).message || '资源加载失败'
    return
  } finally {
    loading.value = false
  }
  // 段落在 loading 结束后才渲染，必须等 DOM 就位再恢复位置
  await nextTick()
  restorePosition()
  startTracking()
}

function restorePosition() {
  const targetSegment = Number(route.query.segment) || resource.value?.progress?.last_segment_id || null
  if (targetSegment) {
    const el = document.querySelector(`[data-segment-id="${targetSegment}"]`)
    if (el) {
      el.scrollIntoView({ block: 'start' })
      if (route.query.segment) {
        highlightSegment.value = targetSegment
        setTimeout(() => { highlightSegment.value = null }, 2600)
      }
      return
    }
  }
  const ratio = resource.value?.progress?.scroll_ratio ?? 0
  if (ratio > 0) {
    const doc = document.documentElement
    doc.scrollTop = ratio * (doc.scrollHeight - doc.clientHeight)
  }
}

function startTracking() {
  lastTick = Date.now()
  saveTimer = setInterval(() => { void flushProgress() }, 15000)
  document.addEventListener('visibilitychange', onVisibility)
  window.addEventListener('scroll', onScroll, { passive: true })
}

function onVisibility() {
  if (document.visibilityState === 'hidden') {
    accumulate()
    void flushProgress()
  } else {
    lastTick = Date.now()
  }
}

function accumulate() {
  const now = Date.now()
  if (document.visibilityState === 'visible' && now > lastTick) {
    pendingReadingMs += Math.min(now - lastTick, 60000)
  }
  lastTick = now
}

function onScroll() {
  dirty = true
  if (scrollDebounce) clearTimeout(scrollDebounce)
  scrollDebounce = setTimeout(() => { void flushProgress() }, 1200)
}

function currentPosition() {
  const doc = document.documentElement
  const ratio = scrollRatio(doc.scrollTop, doc.scrollHeight, doc.clientHeight)
  const tops = Array.from(document.querySelectorAll<HTMLElement>('[data-segment-id]')).map((el) => ({
    id: Number(el.dataset.segmentId),
    top: el.getBoundingClientRect().top,
  }))
  return { ratio, segmentId: nearestSegmentId(tops, 0) }
}

async function flushProgress() {
  if (!resource.value || loading.value) return
  accumulate()
  if (!dirty && pendingReadingMs < 1000) return
  const { ratio, segmentId } = currentPosition()
  const delta = pendingReadingMs
  pendingReadingMs = 0
  dirty = false
  try {
    await saveProgress(resourceId, {
      last_segment_id: segmentId ?? undefined,
      scroll_ratio: ratio,
      reading_ms_delta: delta,
    })
  } catch {
    pendingReadingMs += delta // 失败保留时长，下次重试
    dirty = true
  }
}

// —— 选词收藏 ——
function onMouseUp(event: MouseEvent) {
  const selection = window.getSelection()
  if (!selection || selection.isCollapsed) { popup.value = null; return }
  const term = normalizeSelection(selection.toString())
  if (!term) { popup.value = null; return }
  const anchor = selection.anchorNode?.parentElement?.closest<HTMLElement>('[data-segment-id]')
  if (!anchor) { popup.value = null; return }
  const segmentId = Number(anchor.dataset.segmentId)
  const segment = segments.value.find((item) => item.id === segmentId)
  if (!segment) { popup.value = null; return }
  const offset = segment.content.indexOf(selection.toString().trim())
  popup.value = {
    x: event.clientX,
    y: event.clientY,
    term,
    segmentId,
    offset: offset >= 0 ? offset : 0,
  }
}

async function collect() {
  if (!popup.value || collecting.value) return
  const { term, segmentId, offset } = popup.value
  const segment = segments.value.find((item) => item.id === segmentId)
  if (!segment) return
  collecting.value = true
  const context = extractSentenceContext(segment.content, offset, term.length)
  try {
    const result = await collectWordFromSelection({
      term,
      context_sentence: context.sentence,
      context_before: context.before,
      context_after: context.after,
      resource_id: resourceId,
      segment_id: segmentId,
    })
    showToast(result.merged
      ? `已合并到词条“${result.entry.term}”，新增一条语境`
      : `已收藏“${result.entry.term}”${result.enriched_from_dictionary ? '，词典释义已补全' : ''}`)
    popup.value = null
    window.getSelection()?.removeAllRanges()
  } catch (cause) {
    const error = cause as ApiError
    showToast(error.status === 404 || error.status === 405
      ? '收藏接口还没上线（后端开发中），这次选择没有保存。'
      : `收藏失败：${error.message}`)
  } finally {
    collecting.value = false
  }
}

function showToast(message: string) {
  toast.value = message
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value = '' }, 3600)
}

function headingTag(segment: ResourceSegment): string {
  const level = Math.min(Math.max(segment.heading_level || 2, 2), 4)
  return `h${level}`
}

onMounted(load)

onBeforeUnmount(() => {
  if (saveTimer) clearInterval(saveTimer)
  if (scrollDebounce) clearTimeout(scrollDebounce)
  if (toastTimer) clearTimeout(toastTimer)
  document.removeEventListener('visibilitychange', onVisibility)
  window.removeEventListener('scroll', onScroll)
  void flushProgress()
})
</script>

<template>
  <div class="page reader-page" style="max-width:960px">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
      <button class="button ghost compact" type="button" @click="router.push('/resources')"><ArrowLeft :size="16" />资源库</button>
      <div v-if="resource" style="min-width:0">
        <strong style="font-size:15px;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{ resource.title }}</strong>
        <small style="color:var(--muted)">{{ resource.segment_count }} 段 · 选中文本即可收藏生词</small>
      </div>
    </div>

    <div v-if="loading" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在打开资源…</p></div>

    <div v-else-if="loadError" class="warning">
      {{ loadError }}
      <button class="button ghost compact" type="button" @click="load">重试</button>
    </div>

    <div v-else-if="isNeedsReview" class="card empty">
      <strong>这份资源解析失败，暂时无法阅读</strong>
      <p>原因：{{ resource?.parse_error || '未知' }}。原始文件已完整保留。</p>
      <p>可以回到资源库删除本条后，转换编码或格式再重新导入。</p>
      <RouterLink class="button secondary" to="/resources">回资源库处理</RouterLink>
    </div>

    <article v-else ref="contentEl" class="card reader-content" style="padding:36px clamp(20px,5vw,56px);line-height:1.9;font-size:16.5px" @mouseup="onMouseUp">
      <template v-for="segment in segments" :key="segment.id">
        <component
          :is="headingTag(segment)"
          v-if="segment.kind === 'heading'"
          :data-segment-id="segment.id"
          :class="{ 'segment-flash': highlightSegment === segment.id }"
          style="margin:1.4em 0 .5em"
        >{{ segment.content }}</component>
        <p
          v-else
          :data-segment-id="segment.id"
          :class="{ 'segment-flash': highlightSegment === segment.id }"
          style="margin:0 0 1em;white-space:pre-wrap"
        >{{ segment.content }}</p>
      </template>
      <div v-if="!segments.length" class="empty">这份资源没有可显示的段落。</div>
    </article>

    <button
      v-if="popup"
      class="button compact"
      style="position:fixed;z-index:60;transform:translate(-50%,-130%)"
      :style="`left:${popup.x}px;top:${popup.y}px`"
      type="button"
      :disabled="collecting"
      @click="collect"
    >
      <Loader2 v-if="collecting" :size="14" class="spinning" /><BookMarked v-else :size="14" />收藏“{{ popup.term.length > 24 ? popup.term.slice(0, 24) + '…' : popup.term }}”
    </button>

    <div
      v-if="toast"
      style="position:fixed;left:50%;bottom:34px;transform:translateX(-50%);z-index:70;background:var(--surface-solid);border:1px solid var(--line);border-radius:12px;padding:11px 16px;box-shadow:var(--shadow-sm);font-size:13px"
    >{{ toast }}</div>
  </div>
</template>

<style scoped>
.segment-flash { animation: segment-flash 2.4s ease; border-radius: 6px; }
@keyframes segment-flash {
  0% { background: var(--primary-soft); }
  100% { background: transparent; }
}
.reader-content ::selection { background: var(--primary-soft); }
</style>
