<script setup lang="ts">
import { computed, ref, watch } from 'vue'

const props = defineProps<{
  options: string[]
  correct: string[]        // 规范化前的正确答案（可多个等价）
  disabled?: boolean
}>()

const emit = defineEmits<{ (e: 'answered', payload: { chosen: string, correct: boolean }): void }>()

const chosen = ref<string | null>(null)

watch(() => props.options, () => { chosen.value = null })

const normalized = computed(() => props.correct.map((item) => item.trim().toLowerCase()))

function pick(option: string) {
  if (chosen.value !== null || props.disabled) return
  chosen.value = option
  emit('answered', { chosen: option, correct: normalized.value.includes(option.trim().toLowerCase()) })
}

function stateOf(option: string): 'idle' | 'correct' | 'wrong' | 'reveal' {
  if (chosen.value === null) return 'idle'
  const isCorrect = normalized.value.includes(option.trim().toLowerCase())
  if (option === chosen.value) return isCorrect ? 'correct' : 'wrong'
  return isCorrect ? 'reveal' : 'idle'
}
</script>

<template>
  <div class="option-list" role="listbox" aria-label="选项">
    <button
      v-for="option in props.options"
      :key="option"
      type="button"
      class="option-item"
      :class="stateOf(option)"
      :disabled="props.disabled || chosen !== null"
      @click="pick(option)"
    >{{ option }}</button>
  </div>
</template>

<style scoped>
.option-list { display: grid; gap: 10px; }
.option-item {
  text-align: left; padding: 13px 16px; border-radius: 12px;
  border: 1px solid var(--line-strong); background: var(--surface-solid); color: var(--ink);
  transition: border-color .15s ease, background-color .15s ease;
}
.option-item:hover:not(:disabled) { border-color: var(--primary); }
.option-item.correct, .option-item.reveal { border-color: var(--primary); background: var(--primary-soft); }
.option-item.wrong { border-color: var(--danger); background: var(--danger-soft); }
</style>
