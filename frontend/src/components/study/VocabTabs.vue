<script setup lang="ts">
import { BookMarked, BookOpen, GraduationCap } from 'lucide-vue-next'
</script>

<template>
  <nav class="vocab-tabs" aria-label="词汇模块导航">
    <RouterLink to="/study"><GraduationCap :size="16" />记单词</RouterLink>
    <RouterLink to="/wordbooks"><BookOpen :size="16" />词书与计划</RouterLink>
    <RouterLink to="/vocabulary"><BookMarked :size="16" />生词本</RouterLink>
  </nav>
</template>

<style scoped>
.vocab-tabs {
  display: inline-flex; gap: 4px; padding: 4px;
  border: 1px solid var(--line); border-radius: 13px;
  background: var(--surface-solid); margin-bottom: 20px;
}
.vocab-tabs a {
  display: inline-flex; align-items: center; gap: 7px;
  padding: 8px 16px; border-radius: 10px; font-size: 14px;
  color: var(--muted); transition: background-color .15s ease, color .15s ease;
}
.vocab-tabs a:hover { color: var(--ink); }
.vocab-tabs a.router-link-active {
  background: var(--primary); color: #fff; font-weight: 600;
}
</style>
