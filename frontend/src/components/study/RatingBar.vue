<script setup lang="ts">
import { computed } from 'vue'
import type { Rating } from '../../services/study'

const props = defineProps<{
  suggested?: Rating | null
  disabled?: boolean
  /** subjective=自评（认识/模糊/忘记）；objective=客观判分确认改评；teaching=新词首学三键 */
  mode?: 'subjective' | 'objective' | 'teaching'
}>()

const emit = defineEmits<{ (e: 'rate', rating: Rating): void }>()

const LABELS: Record<'subjective' | 'objective' | 'teaching', Array<{ rating: Rating, label: string }>> = {
  subjective: [
    { rating: 1, label: '忘记' },
    { rating: 2, label: '模糊' },
    { rating: 3, label: '认识' },
    { rating: 4, label: '太简单' },
  ],
  objective: [
    { rating: 1, label: '重来' },
    { rating: 2, label: '困难' },
    { rating: 3, label: '良好' },
    { rating: 4, label: '轻松' },
  ],
  teaching: [
    { rating: 1, label: '有点难' },
    { rating: 3, label: '记住了' },
    { rating: 4, label: '太简单，斩' },
  ],
}

const buttons = computed(() => LABELS[props.mode ?? 'objective'])
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
      {{ item.label }}<kbd>{{ item.rating }}</kbd>
    </button>
  </div>
</template>

<style scoped>
.rating-bar { display: flex; gap: 10px; justify-content: center; flex-wrap: wrap; }
.rating-button { min-width: 84px; }
.rating-button kbd { margin-left: 6px; font-size: 11px; opacity: .65; border: 1px solid currentColor; border-radius: 4px; padding: 0 4px; }
.rating-button.suggested { outline: 2px solid var(--primary); }
</style>
