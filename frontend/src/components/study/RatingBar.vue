<script setup lang="ts">
import type { Rating } from '../../services/study'

const props = defineProps<{
  suggested?: Rating | null
  disabled?: boolean
}>()

const emit = defineEmits<{ (e: 'rate', rating: Rating): void }>()

const buttons: Array<{ rating: Rating, label: string, hint: string }> = [
  { rating: 1, label: '重来', hint: '1' },
  { rating: 2, label: '困难', hint: '2' },
  { rating: 3, label: '良好', hint: '3' },
  { rating: 4, label: '轻松', hint: '4' },
]
</script>

<template>
  <div class="rating-bar" role="group" aria-label="记忆评分">
    <button
      v-for="item in buttons"
      :key="item.rating"
      type="button"
      class="button compact rating-button"
      :class="{ secondary: item.rating !== props.suggested, suggested: item.rating === props.suggested }"
      :disabled="props.disabled"
      :data-rating="item.rating"
      @click="emit('rate', item.rating)"
    >
      {{ item.label }}<kbd>{{ item.hint }}</kbd>
    </button>
  </div>
</template>

<style scoped>
.rating-bar { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
.rating-button { min-width: 84px; }
.rating-button kbd { margin-left: 6px; font-size: 11px; opacity: .65; border: 1px solid currentColor; border-radius: 4px; padding: 0 4px; }
.rating-button.suggested { outline: 2px solid var(--primary); }
</style>
