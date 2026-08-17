<script setup lang="ts">
import { BookOpen, Loader2 } from 'lucide-vue-next'
import { onMounted, ref } from 'vue'
import type { ApiError } from '../api'
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

async function activate(book: Wordbook) {
  busyId.value = book.id
  try {
    await activateWordbookPlan(book.id, { daily_new: dailyNew.value })
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

onMounted(load)
</script>

<template>
  <div class="page" style="max-width:960px">
    <div class="page-head">
      <div>
        <span class="eyebrow">WORDBOOKS</span>
        <h1>词书与计划</h1>
        <p class="lead">选择内置词书（CET4 / CET6 / 考研）或导入自定义词表，设定每日新词量开始学习。</p>
      </div>
    </div>

    <div v-if="loading" class="card empty"><Loader2 :size="20" class="spinning" /><p>正在加载词书…</p></div>

    <div v-else-if="!backendReady" class="card empty">
      <strong>词书接口还在开发中</strong>
      <p>后端 /api/wordbooks 与内置词典包尚未上线（Codex Issue #2 进行中）。上线后内置考研/CET 词书将离线可用。</p>
    </div>

    <template v-else>
      <div v-if="loadError" class="warning">{{ loadError }}<button class="button ghost compact" type="button" @click="load">重试</button></div>

      <div class="card" style="margin-bottom:16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap">
        <label style="color:var(--muted);font-size:13px">每日新词量</label>
        <input v-model.number="dailyNew" type="number" min="0" max="200" style="width:90px;min-height:40px;border:1px solid var(--line-strong);border-radius:10px;padding:6px 10px;background:var(--surface-solid);color:var(--ink)" />
        <span style="color:var(--muted);font-size:12px">激活词书时生效；0 表示只复习不学新词</span>
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
