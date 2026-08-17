<script setup lang="ts">
import { ClipboardPaste, FileUp, FolderOpen, Loader2, RefreshCcw, Search, Trash2, X } from 'lucide-vue-next'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { ApiError } from '../api'
import {
  RESOURCE_STATUS_LABELS,
  RESOURCE_TYPE_LABELS,
  type Resource,
  type SearchHit,
  deleteResource,
  importResourceFile,
  importResourceText,
  listResources,
  searchAll,
  updateResource,
} from '../services/resources'

const router = useRouter()

const items = ref<Resource[]>([])
const total = ref(0)
const loading = ref(false)
const loadError = ref('')
const statusFilter = ref('')
const titleQuery = ref('')

const importing = ref(false)
const importError = ref('')
const duplicateId = ref<number | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const showPaste = ref(false)
const pasteTitle = ref('')
const pasteContent = ref('')
const pasteFormat = ref<'md' | 'txt'>('md')
const pasteType = ref('note')

const searchQuery = ref('')
const searchHits = ref<SearchHit[]>([])
const searching = ref(false)
const searchError = ref('')
const searchDone = ref(false)

const statusOptions = [
  { value: '', label: '全部' },
  { value: 'inbox', label: '收件箱' },
  { value: 'active', label: '学习中' },
  { value: 'archived', label: '已归档' },
  { value: 'needs_review', label: '待处理' },
]

const hasContent = computed(() => items.value.length > 0)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const page = await listResources({ status: statusFilter.value, q: titleQuery.value, limit: 100 })
    items.value = page.items
    total.value = page.total
  } catch (cause) {
    loadError.value = (cause as Error).message || '资源列表加载失败'
  } finally {
    loading.value = false
  }
}

function setStatus(value: string) {
  statusFilter.value = value
  void load()
}

function pickFile() {
  fileInput.value?.click()
}

async function onFileChosen(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  await runImport(() => importResourceFile(file))
}

async function submitPaste() {
  if (!pasteTitle.value.trim() || !pasteContent.value.trim()) {
    importError.value = '标题和正文都不能为空'
    return
  }
  await runImport(() => importResourceText({
    title: pasteTitle.value.trim(),
    content: pasteContent.value,
    type: pasteType.value,
    format: pasteFormat.value,
  }))
  if (!importError.value) {
    showPaste.value = false
    pasteTitle.value = ''
    pasteContent.value = ''
  }
}

async function runImport(action: () => Promise<{ resource: Resource }>) {
  importing.value = true
  importError.value = ''
  duplicateId.value = null
  try {
    const { resource } = await action()
    await load()
    if (resource.status === 'needs_review') {
      importError.value = `已保存原文件，但解析失败：${resource.parse_error || '未知原因'}。可在列表中删除后重新导入。`
    }
  } catch (cause) {
    const error = cause as ApiError
    if (error.code === 'duplicate_resource') {
      duplicateId.value = Number(error.details?.existing_id) || null
      importError.value = '这份文件之前已经导入过。'
    } else {
      importError.value = error.message || '导入失败，原文件未被修改，可重试。'
    }
  } finally {
    importing.value = false
  }
}

async function toggleArchive(item: Resource) {
  try {
    await updateResource(item.id, { status: item.status === 'archived' ? 'active' : 'archived' })
    await load()
  } catch (cause) {
    loadError.value = (cause as Error).message
  }
}

async function removeResource(item: Resource) {
  if (!window.confirm(`把“${item.title}”移入回收站？7 天内可恢复。`)) return
  try {
    await deleteResource(item.id)
    await load()
  } catch (cause) {
    loadError.value = (cause as Error).message
  }
}

async function runSearch() {
  const q = searchQuery.value.trim()
  searchDone.value = false
  searchHits.value = []
  searchError.value = ''
  if (!q) return
  searching.value = true
  try {
    const page = await searchAll(q)
    searchHits.value = page.items
    searchDone.value = true
  } catch (cause) {
    searchError.value = (cause as Error).message || '搜索失败'
  } finally {
    searching.value = false
  }
}

function openHit(hit: SearchHit) {
  void router.push(`/resources/${hit.resource_id}/read?segment=${hit.segment_id}`)
}

function progressPercent(item: Resource): number {
  return Math.round((item.progress?.scroll_ratio ?? 0) * 100)
}

function formatTime(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString()
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="page-head">
      <div>
        <span class="eyebrow">RESOURCE LIBRARY</span>
        <h1>资源库</h1>
        <p class="lead">导入文章、笔记与教材文本，阅读、划词并沉淀到复习。原始文件始终保留。</p>
      </div>
      <div style="display:flex;gap:10px;flex-wrap:wrap">
        <button class="button" type="button" :disabled="importing" @click="pickFile">
          <Loader2 v-if="importing" :size="16" class="spinning" /><FileUp v-else :size="16" />上传 TXT / Markdown
        </button>
        <button class="button secondary" type="button" @click="showPaste = !showPaste">
          <ClipboardPaste :size="16" />粘贴文本
        </button>
        <input ref="fileInput" type="file" accept=".txt,.md,.markdown" style="display:none" @change="onFileChosen" />
      </div>
    </div>

    <div v-if="importError" class="warning">
      {{ importError }}
      <button v-if="duplicateId" class="button ghost compact" type="button" @click="router.push(`/resources/${duplicateId}/read`)">
        打开已有资源
      </button>
    </div>

    <section v-if="showPaste" class="card" style="margin-bottom:18px">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
        <strong>粘贴文本导入</strong>
        <button class="button ghost compact" type="button" aria-label="关闭" @click="showPaste = false"><X :size="15" /></button>
      </div>
      <div class="field"><label>标题</label><input v-model="pasteTitle" maxlength="300" placeholder="例如：Economist 精读 0817" /></div>
      <div class="field"><label>正文（支持 Markdown）</label><textarea v-model="pasteContent" rows="8" placeholder="粘贴英文文章或笔记正文" /></div>
      <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:end">
        <div class="field" style="margin-bottom:0"><label>格式</label>
          <select v-model="pasteFormat"><option value="md">Markdown</option><option value="txt">纯文本</option></select>
        </div>
        <div class="field" style="margin-bottom:0"><label>类型</label>
          <select v-model="pasteType">
            <option value="note">笔记</option><option value="article">文章</option><option value="other">其他</option>
          </select>
        </div>
        <button class="button" type="button" :disabled="importing" @click="submitPaste">保存到资源库</button>
      </div>
    </section>

    <section class="card" style="margin-bottom:18px">
      <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
        <Search :size="17" style="color:var(--muted)" />
        <input
          v-model="searchQuery"
          style="flex:1;min-width:220px;min-height:40px;border:1px solid var(--line-strong);border-radius:10px;padding:8px 12px;background:var(--surface-solid);color:var(--ink)"
          placeholder="全文搜索资源正文（中英文均可）"
          @keyup.enter="runSearch"
        />
        <button class="button secondary compact" type="button" :disabled="searching" @click="runSearch">
          <Loader2 v-if="searching" :size="15" class="spinning" /><span v-else>搜索</span>
        </button>
      </div>
      <div v-if="searchError" class="warning" style="margin-top:10px">{{ searchError }}</div>
      <div v-if="searchDone && !searchHits.length" class="empty" style="padding:18px">
        没有找到“{{ searchQuery }}”。全文索引只覆盖已成功解析的资源。
      </div>
      <ul v-if="searchHits.length" style="list-style:none;margin:12px 0 0;padding:0;display:grid;gap:8px">
        <li v-for="hit in searchHits" :key="`${hit.resource_id}-${hit.segment_id}`">
          <button
            type="button"
            style="width:100%;text-align:left;border:1px solid var(--line);border-radius:12px;padding:12px 14px;background:var(--surface-solid);color:var(--ink)"
            @click="openHit(hit)"
          >
            <strong style="font-size:13px">{{ hit.resource_title }}</strong>
            <span style="color:var(--muted);font-size:12px"> · 第 {{ hit.sequence }} 段</span>
            <p class="search-snippet" style="margin:6px 0 0;font-size:13px;line-height:1.7;color:var(--muted)" v-html="hit.snippet" />
          </button>
        </li>
      </ul>
    </section>

    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:14px">
      <button
        v-for="option in statusOptions"
        :key="option.value"
        type="button"
        class="pill"
        :style="statusFilter === option.value ? 'outline:2px solid var(--primary)' : 'opacity:.75'"
        @click="setStatus(option.value)"
      >{{ option.label }}</button>
      <span style="flex:1" />
      <input
        v-model="titleQuery"
        style="min-height:38px;border:1px solid var(--line-strong);border-radius:10px;padding:6px 12px;background:var(--surface-solid);color:var(--ink)"
        placeholder="按标题筛选"
        @keyup.enter="load"
      />
      <button class="button ghost compact" type="button" aria-label="刷新" @click="load"><RefreshCcw :size="15" /></button>
    </div>

    <div v-if="loadError" class="warning">
      {{ loadError }}
      <button class="button ghost compact" type="button" @click="load">重试</button>
    </div>

    <div v-if="loading && !hasContent" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在加载资源…</p></div>

    <div v-else-if="hasContent" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">
      <article v-for="item in items" :key="item.id" class="card" style="display:flex;flex-direction:column;gap:10px">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <span class="pill">{{ RESOURCE_TYPE_LABELS[item.type] || item.type }}</span>
          <span class="pill" :style="item.status === 'needs_review' ? 'background:var(--danger-soft);color:var(--danger)' : ''">
            {{ RESOURCE_STATUS_LABELS[item.status] }}
          </span>
          <span style="color:var(--muted);font-size:12px">{{ item.segment_count }} 段</span>
        </div>
        <RouterLink :to="`/resources/${item.id}/read`" style="color:var(--ink);text-decoration:none">
          <strong style="font-size:16px;line-height:1.5">{{ item.title }}</strong>
        </RouterLink>
        <p v-if="item.status === 'needs_review'" style="margin:0;font-size:12px;color:var(--danger)">
          解析失败：{{ item.parse_error || '未知原因' }}（原文件已保留）
        </p>
        <div v-if="item.progress" style="display:grid;gap:4px">
          <div style="height:6px;border-radius:999px;background:var(--line);overflow:hidden">
            <div :style="`height:100%;width:${progressPercent(item)}%;background:var(--primary)`" />
          </div>
          <small style="color:var(--muted)">读到 {{ progressPercent(item) }}% · 上次 {{ formatTime(item.progress.last_opened_at) }}</small>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:auto">
          <RouterLink class="button secondary compact" :to="`/resources/${item.id}/read`"><FolderOpen :size="15" />打开阅读</RouterLink>
          <button class="button ghost compact" type="button" @click="toggleArchive(item)">
            {{ item.status === 'archived' ? '取消归档' : '归档' }}
          </button>
          <button class="button ghost danger compact" type="button" @click="removeResource(item)"><Trash2 :size="15" />删除</button>
        </div>
      </article>
    </div>

    <div v-else-if="!loading" class="card empty">
      <strong>资源库还是空的</strong>
      <p>点右上角"上传 TXT / Markdown"导入第一篇文章，或用"粘贴文本"快速保存一段笔记。导入后即可阅读、划词收藏并进入复习。</p>
      <p style="font-size:12px">试试仓库里的示例：test-fixtures/sample-article.txt</p>
    </div>
  </div>
</template>
