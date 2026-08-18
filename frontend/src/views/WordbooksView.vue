<script setup lang="ts">
import { ArrowLeft, BookOpen, FileUp, Loader2 } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import { api, type ApiError } from '../api'
import { type Wordbook, activateWordbookPlan, deactivateWordbookPlan, listWordbooks } from '../services/study'

const items = ref<Wordbook[]>([])
const loading = ref(true)
const backendReady = ref(true)
const loadError = ref('')
const busyId = ref<number | null>(null)
const dailyNew = ref(20)

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    items.value = (await listWordbooks()).items
  } catch (cause) {
    const error = cause as ApiError
    if (error.status === 404 || error.status === 405) backendReady.value = false
    else loadError.value = error.message || '词书列表加载失败'
  } finally {
    loading.value = false
  }
}

const activatedName = ref('')

async function activate(book: Wordbook) {
  busyId.value = book.id
  try {
    await activateWordbookPlan(book.id, { daily_new: dailyNew.value })
    activatedName.value = book.name
    await load()
  } catch (cause) {
    loadError.value = (cause as Error).message
  } finally {
    busyId.value = null
  }
}

async function deactivate(book: Wordbook) {
  busyId.value = book.id
  try {
    await deactivateWordbookPlan(book.id)
    await load()
  } catch (cause) {
    loadError.value = (cause as Error).message
  } finally {
    busyId.value = null
  }
}

// —— 自定义词表导入（VOC-27 前端面 / A8.3；POST /wordbooks/import multipart） ——
const importInput = ref<HTMLInputElement | null>(null)
const importName = ref('')
const importing = ref(false)
const importReport = ref<{ name: string, matched: number, unmatched: Array<{ term: string }> } | null>(null)

function pickImportFile() {
  importInput.value?.click()
}

async function onImportFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (!file) return
  importing.value = true
  loadError.value = ''
  importReport.value = null
  try {
    const form = new FormData()
    form.append('file', file)
    form.append('name', importName.value.trim() || file.name.replace(/\.(txt|csv)$/i, ''))
    const result = await api<{ wordbook: { name: string }, matched: number, unmatched: Array<{ term: string }> }>(
      '/wordbooks/import',
      { method: 'POST', body: form },
    )
    importReport.value = {
      name: result.wordbook?.name ?? importName.value,
      matched: result.matched ?? 0,
      unmatched: result.unmatched ?? [],
    }
    importName.value = ''
    await load()
  } catch (cause) {
    loadError.value = `词表导入失败：${(cause as Error).message}`
  } finally {
    importing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="vocab-pane">
    <p class="lead" style="margin:0 0 14px">选择内置词书（CET4 / CET6 / 考研）或导入自定义词表，设定每日新词量开始学习。</p>

    <div v-if="loading" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在加载词书…</p></div>

    <div v-else-if="!backendReady" class="card empty">
      <strong>词书接口还在开发中</strong>
      <p>后端 /api/wordbooks 与内置词典包尚未上线（Codex Issue #2 进行中）。上线后内置考研/CET 词书将离线可用。</p>
    </div>

    <template v-else>
      <div v-if="loadError" class="warning">{{ loadError }}<button class="button ghost compact" type="button" @click="load">重试</button></div>

      <div v-if="activatedName" class="card" style="margin-bottom:16px;display:flex;gap:12px;align-items:center;background:var(--primary-soft)">
        <span>已把「{{ activatedName }}」设为学习计划，每天 {{ dailyNew }} 个新词。</span>
        <RouterLink class="button compact" to="/study"><ArrowLeft :size="15" style="transform:rotate(180deg)" />现在开始学</RouterLink>
      </div>

      <div class="card" style="margin-bottom:16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
        <label style="color:var(--muted);font-size:13px">每日新词量</label>
        <input v-model.number="dailyNew" type="number" min="0" max="200" style="width:90px;min-height:40px;border:1px solid var(--line-strong);border-radius:10px;padding:6px 10px;background:var(--surface-solid);color:var(--ink)" />
        <span style="color:var(--muted);font-size:12px">激活词书时生效；0 表示只复习不学新词</span>
        <span style="flex:1" />
        <input v-model="importName" placeholder="词表名称（可选）" style="width:150px;min-height:40px;border:1px solid var(--line-strong);border-radius:10px;padding:6px 10px;background:var(--surface-solid);color:var(--ink)" />
        <button class="button secondary compact" type="button" :disabled="importing" @click="pickImportFile">
          <Loader2 v-if="importing" :size="15" class="spinning" /><FileUp v-else :size="15" />导入词表（TXT/CSV）
        </button>
        <input ref="importInput" type="file" accept=".txt,.csv" style="display:none" @change="onImportFile" />
      </div>

      <div v-if="importReport" class="card" style="margin-bottom:16px;display:grid;gap:8px;background:var(--primary-soft)">
        <strong>「{{ importReport.name }}」导入完成：匹配词典 {{ importReport.matched }} 个<template v-if="importReport.unmatched.length">，未匹配 {{ importReport.unmatched.length }} 个</template></strong>
        <p v-if="importReport.unmatched.length" style="margin:0;font-size:13px;color:var(--muted)">
          未匹配的词已按你文件里的释义建档（无释义的待补全，不会生成空白卡片）：
          {{ importReport.unmatched.slice(0, 10).map(u => u.term).join('、') }}<template v-if="importReport.unmatched.length > 10"> 等 {{ importReport.unmatched.length }} 个</template>
        </p>
        <div><button class="button compact" type="button" @click="importReport = null">知道了</button></div>
      </div>

      <div v-if="items.length" class="grid" style="grid-template-columns:repeat(auto-fill,minmax(280px,1fr))">
        <article v-for="book in items" :key="book.id" class="card" style="display:grid;gap:10px">
          <div style="display:flex;gap:8px;align-items:center">
            <BookOpen :size="18" style="color:var(--primary)" />
            <strong>{{ book.name }}</strong>
            <span class="pill">{{ book.kind === 'builtin' ? '内置' : '导入' }}</span>
          </div>
          <small style="color:var(--muted)">
            共 {{ book.total }} 词 · 学习中 {{ book.state_counts.learning }} · 已掌握 {{ book.state_counts.known }}
          </small>
          <div style="display:flex;gap:8px;margin-top:4px">
            <button v-if="!book.active_plan" class="button compact" type="button" :disabled="busyId === book.id" @click="activate(book)">
              <Loader2 v-if="busyId === book.id" :size="14" class="spinning" /><span v-else>设为学习计划</span>
            </button>
            <template v-else>
              <span class="pill" style="align-self:center">当前计划</span>
              <button class="button ghost compact" type="button" :disabled="busyId === book.id" @click="deactivate(book)">停用</button>
            </template>
          </div>
        </article>
      </div>
      <div v-else class="card empty"><strong>还没有词书</strong><p>内置词书随词典包提供；也可以导入自己的词表（功能随后端上线）。</p></div>
    </template>
  </div>
</template>
