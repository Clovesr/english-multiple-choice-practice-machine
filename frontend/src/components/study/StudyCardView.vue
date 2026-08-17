<script setup lang="ts">
import { Lightbulb, Volume2 } from 'lucide-vue-next'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  type Rating,
  type StudyCard,
  buildHint,
  highlightSegments,
  isObjectiveCard,
  isRecallReverse,
  judgeLocally,
  lookupDictionary,
  ratingFromKey,
  suggestedRating,
} from '../../services/study'
import { speak } from '../../services/speech'
import OptionList from './OptionList.vue'
import RatingBar from './RatingBar.vue'

const props = defineProps<{
  card: StudyCard
  speechAvailable: boolean
  /** 当日重练副本（错词内循环），评分只做本地清障 */
  drill?: boolean
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
const hintLevel = ref(0)
const dictExtra = ref<Record<string, any> | null>(null)

const typeLabels: Record<StudyCard['card_type'], string> = {
  forward: '认词', reverse: '辨义', listening: '听音', spelling: '拼写', cloze: '语境填词', collocation: '搭配',
}

const TAG_LABELS: Record<string, string> = {
  zk: '中考', gk: '高考', cet4: 'CET4', cet6: 'CET6', ky: '考研', toefl: '托福', ielts: '雅思', gre: 'GRE',
}

const objective = computed(() => isObjectiveCard(props.card) && !isRecallReverse(props.card))
const recallReverse = computed(() => isRecallReverse(props.card))
const suggested = computed<Rating | null>(() =>
  objective.value && localCorrect.value !== null ? suggestedRating(localCorrect.value) : null)

/** 新词首照面 = 教学模式：先教后测（百词斩式），直接展示全部内容 */
const teaching = computed(() =>
  props.card.state === 'new' && !props.drill
  && (props.card.card_type === 'forward' || recallReverse.value))

const ratingMode = computed<'subjective' | 'objective'>(() => (objective.value ? 'objective' : 'subjective'))

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

/** 需要遮住答案的卡型（拼写/听音/挖空）在作答前隐藏提示区里的词形 */
const hidesWord = computed(() => ['spelling', 'listening', 'cloze'].includes(props.card.card_type))

const hint = computed(() => buildHint(
  hintLevel.value,
  props.card.entry.lemma,
  props.card.entry.phonetic_us || props.card.entry.phonetic_uk,
))

/** 高亮目标：原形 + 词典词形变化 */
const highlightTargets = computed(() => {
  const forms: string[] = (dictExtra.value?.forms ?? []).map((f: any) => String(f.text))
  return [props.card.entry.lemma, ...forms]
})

const examTags = computed<string[]>(() =>
  (dictExtra.value?.tags ?? []).map((t: string) => TAG_LABELS[t] || t.toUpperCase()))

const wordForms = computed<Array<{ kind: string, text: string }>>(() => dictExtra.value?.forms ?? [])
const relations = computed<Array<{ relation_type: string, related_term: string }>>(
  () => (dictExtra.value?.relations ?? []).slice(0, 6))

const FORM_LABELS: Record<string, string> = {
  p: '过去式', d: '过去分词', i: '现在分词', '3': '第三人称', r: '比较级', t: '最高级', s: '复数', '0': '原形', '1': '原形变体',
}

function initCard() {
  revealed.value = false
  typedAnswer.value = ''
  localCorrect.value = null
  answerGiven.value = undefined
  startedAt.value = Date.now()
  hintLevel.value = 0
  dictExtra.value = null
  if (teaching.value) {
    // 教学模式：直接翻开 + 自动发音
    revealed.value = true
    void fetchDictExtra()
    if (props.speechAvailable) void playAudio()
  } else if (props.card.card_type === 'listening') {
    void playAudio()
  }
}

watch(() => [props.card.card_id, props.drill], initCard)
onMounted(initCard)

function playAudio(accent: 'us' | 'uk' = 'us') {
  const text = props.card.prompt.tts_text || props.card.entry.lemma
  return speak(text, accent)
}

function speakSentence(sentence: string) {
  void speak(sentence, 'us', 0.92)
}

async function fetchDictExtra() {
  try {
    const result = await lookupDictionary(props.card.entry.lemma)
    if (result.found) dictExtra.value = result.entry
  } catch { /* 词典补充失败不影响学习 */ }
}

function submitTyped() {
  if (localCorrect.value !== null || !typedAnswer.value.trim()) return
  answerGiven.value = typedAnswer.value
  localCorrect.value = judgeLocally(typedAnswer.value, props.card.answer.accept) ?? null
  reveal()
}

function onOptionAnswered(payload: { chosen: string, correct: boolean }) {
  answerGiven.value = payload.chosen
  localCorrect.value = payload.correct
  reveal()
}

function reveal() {
  revealed.value = true
  void fetchDictExtra()
}

function moreHint() {
  hintLevel.value = Math.min(hintLevel.value + 1, 2)
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
      <span v-if="teaching" class="pill" style="background:var(--lavender)">新词学习</span>
      <span v-else class="pill">{{ typeLabels[card.card_type] }}</span>
      <span v-if="drill" class="pill" style="background:var(--apricot)">重练</span>
      <span style="flex:1" />
      <button
        v-if="hidesWord && !revealed"
        class="button ghost compact"
        type="button"
        @click="moreHint"
      ><Lightbulb :size="14" />提示</button>
    </header>

    <!-- 提示区 -->
    <section class="study-prompt">
      <template v-if="card.card_type === 'forward'">
        <h2 class="study-word">{{ card.entry.lemma }}</h2>
        <div class="phonetic-row">
          <button v-if="speechAvailable" class="phonetic-chip" type="button" @click="playAudio('uk')">
            <Volume2 :size="13" />英 <template v-if="card.entry.phonetic_uk">/{{ card.entry.phonetic_uk }}/</template>
          </button>
          <span v-else-if="card.entry.phonetic_uk" class="phonetic-chip static">英 /{{ card.entry.phonetic_uk }}/</span>
          <button v-if="speechAvailable" class="phonetic-chip" type="button" @click="playAudio('us')">
            <Volume2 :size="13" />美 <template v-if="card.entry.phonetic_us">/{{ card.entry.phonetic_us }}/</template>
          </button>
          <span v-else-if="card.entry.phonetic_us" class="phonetic-chip static">美 /{{ card.entry.phonetic_us }}/</span>
        </div>
      </template>

      <template v-else-if="card.card_type === 'reverse'">
        <p class="study-gloss">{{ card.prompt.text }}</p>
      </template>

      <template v-else-if="card.card_type === 'listening'">
        <button class="button secondary" type="button" :disabled="!speechAvailable" @click="playAudio('us')">
          <Volume2 :size="17" />再听一遍
        </button>
        <p v-if="!speechAvailable" class="study-degraded">当前没有可用的英语语音，本卡可跳过（不影响其他卡片）。</p>
      </template>

      <template v-else-if="card.card_type === 'spelling'">
        <p class="study-gloss">{{ card.prompt.text }}</p>
        <button v-if="speechAvailable" class="button ghost compact" type="button" @click="playAudio('us')"><Volume2 :size="15" />听发音</button>
      </template>

      <template v-else-if="card.card_type === 'cloze'">
        <p class="study-cloze">
          {{ clozeParts[0] }}<span class="cloze-blank">{{ revealed ? (card.answer.text ?? '') : '______' }}</span>{{ clozeParts[1] }}
        </p>
      </template>

      <template v-else>
        <p class="study-gloss">{{ card.prompt.text || `选择与 “${card.entry.lemma}” 搭配的表达` }}</p>
      </template>

      <p v-if="hint && !revealed" class="hint-line">{{ hint }}</p>
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
        <button v-if="!revealed && !teaching" class="button" type="button" @click="reveal">显示答案<kbd class="key-hint">空格</kbd></button>
      </template>
    </section>

    <!-- 词详情区（翻面后） -->
    <section v-if="revealed" class="study-reveal">
      <div class="reveal-word-row">
        <strong class="reveal-word">{{ card.entry.lemma }}</strong>
        <div class="phonetic-row">
          <button v-if="speechAvailable" class="phonetic-chip" type="button" @click="playAudio('uk')">
            <Volume2 :size="13" />英 <template v-if="card.entry.phonetic_uk">/{{ card.entry.phonetic_uk }}/</template>
          </button>
          <button v-if="speechAvailable" class="phonetic-chip" type="button" @click="playAudio('us')">
            <Volume2 :size="13" />美 <template v-if="card.entry.phonetic_us">/{{ card.entry.phonetic_us }}/</template>
          </button>
        </div>
        <div v-if="examTags.length" class="tag-row">
          <span v-for="tag in examTags" :key="tag" class="exam-tag">{{ tag }}</span>
        </div>
      </div>

      <ul class="sense-list">
        <li v-for="(sense, i) in card.entry.senses" :key="i">
          <em v-if="sense.pos" class="pos-chip">{{ sense.pos }}</em>
          <div class="sense-body">
            <span class="sense-zh">{{ sense.gloss_zh }}</span>
            <span v-if="sense.gloss_en" class="sense-en">{{ sense.gloss_en.split('\n')[0] }}</span>
          </div>
        </li>
      </ul>

      <p v-if="localCorrect === false && card.answer.text" class="correct-answer">正确答案：{{ card.answer.text }}</p>

      <div v-if="wordForms.length" class="detail-row">
        <label>词形变化</label>
        <div class="form-chips">
          <span v-for="form in wordForms" :key="form.kind + form.text" class="form-chip">
            <small>{{ FORM_LABELS[form.kind] || form.kind }}</small>{{ form.text }}
          </span>
        </div>
      </div>

      <div v-if="relations.length" class="detail-row">
        <label>相关词</label>
        <div class="form-chips">
          <span v-for="rel in relations" :key="rel.related_term" class="form-chip">
            <small>{{ rel.relation_type === 'phrasal_verb' ? '短语' : rel.relation_type === 'collocation' ? '搭配' : '相关' }}</small>{{ rel.related_term }}
          </span>
        </div>
      </div>

      <div v-if="card.contexts.length" class="detail-row">
        <label>你的语境</label>
        <blockquote v-for="(ctx, i) in card.contexts.slice(0, 2)" :key="i" class="context-quote">
          <p>
            <template v-for="(seg, j) in highlightSegments(ctx.sentence, highlightTargets)" :key="j">
              <mark v-if="seg.hit">{{ seg.text }}</mark><template v-else>{{ seg.text }}</template>
            </template>
            <button v-if="speechAvailable" class="phonetic-chip inline-speak" type="button" aria-label="朗读例句" @click="speakSentence(ctx.sentence)"><Volume2 :size="12" /></button>
          </p>
          <cite v-if="ctx.source">{{ ctx.source }}</cite>
        </blockquote>
      </div>

      <RatingBar :suggested="suggested" :mode="teaching ? 'teaching' : ratingMode" @rate="rate" />
      <p class="rating-note" v-if="teaching">这是新词的第一次见面：记住了就继续，之后会按记忆节奏用不同题型考你。</p>
      <p class="rating-note" v-else-if="suggested">已按判定结果建议评分，可自行改选（1-4 键）。</p>
    </section>

    <footer class="study-foot">
      <button class="button ghost compact" type="button" @click="emit('skip')">跳过本卡</button>
    </footer>
  </article>
</template>

<style scoped>
.study-card { max-width: 680px; margin: 0 auto; display: grid; gap: 18px; padding: 30px 34px; }
.study-card-head { display: flex; gap: 8px; align-items: center; }
.study-prompt { text-align: center; display: grid; gap: 12px; justify-items: center; }
.study-word { font: 600 44px Cambria, Georgia, serif; margin: 6px 0 0; letter-spacing: .01em; }
.phonetic-row { display: inline-flex; gap: 8px; flex-wrap: wrap; justify-content: center; }
.phonetic-chip {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 12px; border-radius: 999px; font-size: 13px;
  border: 1px solid var(--line); background: var(--surface-solid); color: var(--muted);
  transition: border-color .15s ease, color .15s ease;
}
.phonetic-chip:hover:not(.static) { border-color: var(--primary); color: var(--primary); }
.phonetic-chip.static { cursor: default; }
.inline-speak { padding: 2px 7px; margin-left: 6px; vertical-align: middle; }
.hint-line { color: var(--primary); font-size: 15px; letter-spacing: .12em; margin: 0; }
.study-gloss { font-size: 19px; line-height: 1.7; margin: 6px 0; max-width: 46ch; }
.study-cloze { font-size: 18px; line-height: 1.9; text-align: left; }
.cloze-blank { color: var(--primary); font-weight: 650; padding: 0 4px; border-bottom: 2px solid var(--primary); }
.study-degraded { color: var(--muted); font-size: 13px; }
.study-answer-zone { display: grid; justify-items: center; gap: 10px; }
.spelling-form { display: flex; gap: 10px; width: 100%; max-width: 420px; }
.spelling-input { flex: 1; min-height: 46px; border: 1px solid var(--line-strong); border-radius: 12px; padding: 10px 14px; font-size: 17px; background: var(--surface-solid); color: var(--ink); text-align: center; }
.spelling-input.ok { border-color: var(--primary); background: var(--primary-soft); }
.spelling-input.bad { border-color: var(--danger); background: var(--danger-soft); }

.study-reveal { border-top: 1px dashed var(--line); padding-top: 18px; display: grid; gap: 16px; }
.reveal-word-row { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.reveal-word { font: 600 26px Cambria, Georgia, serif; }
.tag-row { display: inline-flex; gap: 5px; margin-left: auto; }
.exam-tag { font-size: 10.5px; padding: 3px 8px; border-radius: 999px; background: var(--primary-soft); color: var(--primary); font-weight: 650; }
.sense-list { margin: 0; padding: 0; list-style: none; display: grid; gap: 8px; }
.sense-list li { display: flex; gap: 10px; align-items: baseline; }
.pos-chip { flex: 0 0 auto; font-style: normal; font-size: 12px; color: var(--primary); background: var(--primary-soft); border-radius: 6px; padding: 2px 7px; }
.sense-body { display: grid; gap: 2px; }
.sense-zh { font-size: 16px; line-height: 1.6; }
.sense-en { font-size: 12.5px; color: var(--muted); line-height: 1.55; }
.correct-answer { color: var(--danger); margin: 0; font-weight: 600; }
.detail-row { display: grid; gap: 7px; }
.detail-row > label { font-size: 12px; color: var(--muted); letter-spacing: .05em; }
.form-chips { display: flex; gap: 7px; flex-wrap: wrap; }
.form-chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 4px 10px; border-radius: 8px; font-size: 13px;
  background: var(--surface-solid); border: 1px solid var(--line);
}
.form-chip small { color: var(--muted); font-size: 10.5px; }
.context-quote { margin: 0; padding: 10px 14px; border-left: 3px solid var(--primary-soft); background: color-mix(in srgb, var(--primary-soft) 30%, transparent); border-radius: 0 10px 10px 0; }
.context-quote p { margin: 0; font-size: 14.5px; line-height: 1.75; }
.context-quote mark { background: transparent; color: var(--primary); font-weight: 700; }
.context-quote cite { display: block; margin-top: 5px; color: var(--muted); font-size: 12px; font-style: normal; }
.rating-note { text-align: center; color: var(--muted); font-size: 12px; margin: 0; }
.study-foot { display: flex; justify-content: center; }
.key-hint { margin-left: 8px; font-size: 11px; opacity: .7; border: 1px solid currentColor; border-radius: 4px; padding: 0 5px; }
</style>
