<script setup lang="ts">
import { BookMarked, BookOpen, Flame, GraduationCap } from 'lucide-vue-next'
import { onMounted, provide, ref } from 'vue'
import { get } from '../api'
import type { StudyOverview } from '../services/study'

// 词汇模块统一外壳：标题、分段标签、常驻数据条稳定不动，标签只切换内容区。
const overview = ref<StudyOverview | null>(null)

async function refreshOverview() {
  try {
    overview.value = await get<StudyOverview>('/study/overview')
  } catch { /* 数据条加载失败不阻塞模块 */ }
}

provide('vocabOverview', { overview, refresh: refreshOverview })

function percent(done: number, target: number): number {
  return target > 0 ? Math.min(100, Math.round((done / target) * 100)) : 0
}

onMounted(refreshOverview)
</script>

<template>
  <div class="page vocab-module">
    <header class="vocab-module-head">
      <div>
        <span class="eyebrow">VOCABULARY</span>
        <h1>词汇</h1>
      </div>
      <nav class="vocab-seg" aria-label="词汇模块导航">
        <RouterLink to="/study"><GraduationCap :size="17" /><span>记单词</span></RouterLink>
        <RouterLink to="/wordbooks"><BookOpen :size="17" /><span>词书与计划</span></RouterLink>
        <RouterLink to="/vocabulary"><BookMarked :size="17" /><span>生词本</span></RouterLink>
      </nav>
    </header>

    <div v-if="overview" class="card vocab-module-strip">
      <div class="ov-item">
        <small>今日新学</small>
        <strong>{{ overview.today.new_done }}/{{ overview.today.new_target }}</strong>
        <div class="ov-bar"><div :style="`width:${percent(overview.today.new_done, overview.today.new_target)}%`" /></div>
      </div>
      <div class="ov-item"><small>今日复习</small><strong>{{ overview.today.reviews_done }}</strong></div>
      <div class="ov-item"><small>待复习</small><strong>{{ overview.today.due_left }}</strong></div>
      <div class="ov-item"><small>逾期积压</small><strong :style="overview.overdue_total > 0 ? 'color:var(--danger)' : ''">{{ overview.overdue_total }}</strong></div>
      <div class="ov-item"><small>连续天数</small><strong><Flame :size="14" style="color:var(--primary);vertical-align:-2px" /> {{ overview.streak_days }}</strong></div>
      <div class="ov-item"><small>30日保持率</small><strong>{{ overview.retention_30d === null ? '—' : Math.round(overview.retention_30d * 100) + '%' }}</strong></div>
      <div class="ov-item" v-if="overview.leeches"><small>顽固卡</small><strong style="color:var(--danger)">{{ overview.leeches }}</strong></div>
    </div>

    <RouterView />
  </div>
</template>

<style scoped>
.vocab-module { width: min(100%, 1240px); }
.vocab-module-head {
  display: flex; justify-content: space-between; align-items: flex-end;
  gap: 20px; flex-wrap: wrap; margin-bottom: 18px;
}
.vocab-module-head h1 { margin: 2px 0 0; }
.vocab-seg {
  display: inline-flex; gap: 4px; padding: 5px;
  border: 1px solid var(--line); border-radius: 15px;
  background: var(--surface-solid); box-shadow: var(--shadow-sm);
}
.vocab-seg a {
  display: inline-flex; align-items: center; gap: 8px;
  padding: 10px 22px; border-radius: 11px; font-size: 14.5px;
  color: var(--muted); transition: background-color .15s ease, color .15s ease;
}
.vocab-seg a:hover { color: var(--ink); }
.vocab-seg a.router-link-active { background: var(--primary); color: #fff; font-weight: 650; }
.vocab-module-strip {
  display: flex; gap: 28px; flex-wrap: wrap;
  padding: 14px 22px; margin-bottom: 22px;
}
.ov-item { display: grid; gap: 3px; min-width: 74px; }
.ov-item small { color: var(--muted); font-size: 11px; }
.ov-item strong { font-size: 17px; }
.ov-bar { height: 4px; width: 74px; border-radius: 999px; background: var(--line); overflow: hidden; }
.ov-bar div { height: 100%; background: var(--primary); }
</style>
