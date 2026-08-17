<script setup lang="ts">
import { Volume2 } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  type Rating,
  type StudyCard,
  isObjectiveCard,
  isRecallReverse,
  judgeLocally,
  ratingFromKey,
  suggestedRating,
} from '../../services/study'
import { speak } from '../../services/speech'
import OptionList from './OptionList.vue'
import RatingBar from './RatingBar.vue'

const props = defineProps<{
  card: StudyCard
  speechAvailable: boolean
}>()

const emit = defineEmits<{
  (e: 'grade', payload: { rating: Rating, answer_given?: string, duration_ms: number }): void
  (e: 'skip'): void
}>()

const revealed = ref(false)
const typedAnswer = ref('')
const localCorrect = ref<boolean | null>(null)
const answerGiven = ref<string | undefined>(undefined)
const startedAt = ref(Date.now())

const typeLabels: Record<StudyCard['card_type'], string> = {
  forward: '认词', reverse: '辨义', listening: '听音', spelling: '拼写', cloze: '语境填词', collocation: '搭配',
}

const objective = computed(() => isObjectiveCard(props.card) && !isRecallReverse(props.card))
const recallReverse = computed(() => isRecallReverse(props.card))
const suggested = computed<Rating | null>(() =>
  objective.value && localCorrect.value !== null ? suggestedRating(localCorrect.value) : null)

/** 反向/搭配卡的选项 = 正确答案 + 干扰项，洗牌展示 */
const options = computed(() => {
  const base = [props.card.answer.text ?? '', ...(props.card.answer.distractors ?? [])].filter(Boolean)
  return [...base].sort(() => Math.random() - 0.5)
})

const clozeParts = computed(() => {
  const sentence = props.card.prompt.cloze_sentence ?? ''
  const parts = sentence.split(/_{2,}|｛｛blank｝｝|\{\{blank\}\}/)
  return parts.length >= 2 ? parts : [sentence, '']
})

watch(() => props.card.card_id, () => {
  revealed.value = false
  typedAnswer.value = ''
  localCorrect.value = null
  answerGiven.value = undefined
  startedAt.value = Date.now()
  if (props.card.card_type === 'listening') void playAudio()
})

function playAudio() {
  const text = props.card.prompt.tts_text || props.card.entry.lemma
  return speak(text, 'us')
}

function submitTyped() {
  if (localCorrect.value !== null || !typedAnswer.value.trim()) return
  answerGiven.value = typedAnswer.value
  localCorrect.value = judgeLocally(typedAnswer.value, props.card.answer.accept) ?? null
  revealed.value = true
}

function onOptionAnswered(payload: { chosen: string, correct: boolean }) {
  answerGiven.value = payload.chosen
  localCorrect.value = payload.correct
  revealed.value = true
}

function reveal() {
  revealed.value = true
}

function rate(rating: Rating) {
  emit('grade', {
    rating,
    answer_given: answerGiven.value,
    duration_ms: Math.min(Date.now() - startedAt.value, 300000),
  })
}

function onKey(event: KeyboardEvent) {
  if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return
  if (event.key === ' ' && !revealed.value) { event.preventDefault(); reveal(); return }
  const rating = ratingFromKey(event.key)
  if (rating && revealed.value) rate(rating)
}

onMounted(() => window.addEventListener('keydown', onKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onKey))
</script>

<template>
  <article class="card study-card">
    <header class="study-card-head">
      <span class="pill">{{ typeLabels[card.card_type] }}</span>
      <span v-if="card.state === 'new'" class="pill" style="background:var(--lavender)">新卡</span>
    </header>

    <!-- 提示区 -->
    <section class="study-prompt">
      <template v-if="card.card_type === 'forward'">
        <h2 class="study-word">{{ card.entry.lemma }}</h2>
        <p class="study-phonetic">
          <span v-if="card.entry.phonetic_uk">英 /{{ card.entry.phonetic_uk }}/</span>
          <span v-if="card.entry.phonetic_us">美 /{{ card.entry.phonetic_us }}/</span>
          <button v-if="speechAvailable" class="button ghost compact" type="button" aria-label="朗读" @click="playAudio"><Volume2 :size="15" /></button>
        </p>
      </template>

      <template v-else-if="card.card_type === 'reverse'">
        <p class="study-gloss">{{ card.prompt.text }}</p>
      </template>

      <template v-else-if="card.card_type === 'listening'">
        <button class="button secondary" type="button" :disabled="!speechAvailable" @click="playAudio">
          <Volume2 :size="17" />再听一遍
        </button>
        <p v-if="!speechAvailable" class="study-degraded">当前没有可用的英语语音，本卡可跳过（不影响其他卡片）。</p>
      </template>

      <template v-else-if="card.card_type === 'spelling'">
        <p class="study-gloss">{{ card.prompt.text }}</p>
        <button v-if="speechAvailable" class="button ghost compact" type="button" @click="playAudio"><Volume2 :size="15" />听发音</button>
      </template>

      <template v-else-if="card.card_type === 'cloze'">
        <p class="study-cloze">
          {{ clozeParts[0] }}<span class="cloze-blank">{{ revealed ? (card.answer.text ?? '') : '______' }}</span>{{ clozeParts[1] }}
        </p>
      </template>

      <template v-else>
        <p class="study-gloss">{{ card.prompt.text || `选择与 “${card.entry.lemma}” 搭配的表达` }}</p>
      </template>
    </section>

    <!-- 作答区 -->
    <section class="study-answer-zone">
      <template v-if="(card.card_type === 'reverse' && !recallReverse) || card.card_type === 'collocation'">
        <OptionList :options="options" :correct="[card.answer.text ?? '', ...(card.answer.accept ?? [])]" @answered="onOptionAnswered" />
      </template>

      <template v-else-if="card.card_type === 'spelling' || card.card_type === 'cloze'">
        <form class="spelling-form" @submit.prevent="submitTyped">
          <input
            v-model="typedAnswer"
            :disabled="localCorrect !== null"
            class="spelling-input"
            :class="{ ok: localCorrect === true, bad: localCorrect === false }"
            autocomplete="off" autocapitalize="off" spellcheck="false"
            :placeholder="card.card_type === 'spelling' ? '输入单词，回车判定' : '填入空缺的词'"
          />
          <button class="button compact" type="submit" :disabled="localCorrect !== null || !typedAnswer.trim()">判定</button>
        </form>
      </template>

      <template v-else>
        <button v-if="!revealed" class="button" type="button" @click="reveal">显示答案<kbd class="key-hint">空格</kbd></button>
      </template>
    </section>

    <!-- 答案与评分区 -->
    <section v-if="revealed" class="study-reveal">
      <div v-if="card.card_type !== 'forward'" class="reveal-word">
        <strong>{{ card.entry.lemma }}</strong>
        <span v-if="card.entry.phonetic_us" class="study-phonetic">/{{ card.entry.phonetic_us }}/</span>
      </div>
      <ul class="sense-list">
        <li v-for="(sense, i) in card.entry.senses" :key="i"><em v-if="sense.pos">{{ sense.pos }}</em> {{ sense.gloss_zh }}</li>
      </ul>
      <p v-if="localCorrect === false && card.answer.text" class="correct-answer">正确答案：{{ card.answer.text }}</p>
      <blockquote v-for="(ctx, i) in card.contexts.slice(0, 2)" :key="i" class="context-quote">
        {{ ctx.sentence }}<cite v-if="ctx.source"> —— {{ ctx.source }}</cite>
      </blockquote>
      <RatingBar :suggested="suggested" @rate="rate" />
      <p class="rating-note" v-if="suggested">已按判定结果建议评分，可自行改选（1-4 键）。</p>
    </section>

    <footer class="study-foot">
      <button class="button ghost compact" type="button" @click="emit('skip')">跳过本卡</button>
    </footer>
  </article>
</template>

<style scoped>
.study-card { max-width: 640px; margin: 0 auto; display: grid; gap: 18px; padding: 30px; }
.study-card-head { display: flex; gap: 8px; }
.study-prompt { text-align: center; display: grid; gap: 10px; justify-items: center; }
.study-word { font-size: 40px; margin: 8px 0 0; letter-spacing: .01em; }
.study-phonetic { color: var(--muted); display: inline-flex; gap: 14px; align-items: center; margin: 0; }
.study-gloss { font-size: 19px; line-height: 1.7; margin: 6px 0; }
.study-cloze { font-size: 18px; line-height: 1.9; text-align: left; }
.cloze-blank { color: var(--primary); font-weight: 650; padding: 0 4px; border-bottom: 2px solid var(--primary); }
.study-degraded { color: var(--muted); font-size: 13px; }
.study-answer-zone { display: grid; justify-items: center; gap: 10px; }
.spelling-form { display: flex; gap: 10px; width: 100%; max-width: 420px; }
.spelling-input { flex: 1; min-height: 46px; border: 1px solid var(--line-strong); border-radius: 12px; padding: 10px 14px; font-size: 17px; background: var(--surface-solid); color: var(--ink); text-align: center; }
.spelling-input.ok { border-color: var(--primary); background: var(--primary-soft); }
.spelling-input.bad { border-color: var(--danger); background: var(--danger-soft); }
.study-reveal { border-top: 1px dashed var(--line); padding-top: 16px; display: grid; gap: 12px; }
.reveal-word { display: flex; gap: 12px; align-items: baseline; font-size: 22px; }
.sense-list { margin: 0; padding-left: 18px; line-height: 1.8; }
.sense-list em { color: var(--muted); font-style: normal; margin-right: 6px; }
.correct-answer { color: var(--danger); margin: 0; }
.context-quote { margin: 0; padding: 10px 14px; border-left: 3px solid var(--line-strong); color: var(--muted); font-size: 14px; line-height: 1.7; }
.rating-note { text-align: center; color: var(--muted); font-size: 12px; margin: 0; }
.study-foot { display: flex; justify-content: center; }
.key-hint { margin-left: 8px; font-size: 11px; opacity: .7; border: 1px solid currentColor; border-radius: 4px; padding: 0 5px; }
</style>
