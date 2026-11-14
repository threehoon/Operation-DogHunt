<script setup lang="ts">
// 服务端算好的分析卡：每场一行，先给让分和大小分的倾向、把握和预计范围；模型的文字解释在下面接着出现。
import type { AnalysisCard, AnalysisLean } from '@/types/api'
import { dateTime, signed, team } from '@/components/odds/format'

defineProps<{ cards: AnalysisCard[] }>()

const tone = { 高: 'lean-conf--high', 中: 'lean-conf--mid', 低: 'lean-conf--low' } as const

function short(name: string): string {
  return team(name)[0]
}

function spreadSide(lean: AnalysisLean): string {
  return lean.point === null ? short(lean.side) : `${short(lean.side)} ${signed(lean.point)}`
}

function totalSide(lean: AnalysisLean): string {
  const word = lean.side === 'Over' ? '大分' : '小分'
  return lean.point === null ? word : `${word} ${lean.point}`
}

/** expected 是主队净胜分范围，换成“谁赢几分”的说法。 */
function margin(card: AnalysisCard, [low, high]: [number, number]): string {
  if (low >= 0) return `${short(card.home)}赢 ${low}–${high} 分`
  if (high <= 0) return `${short(card.away)}赢 ${-high}–${-low} 分`
  return `${short(card.away)}赢 ${-low} 到${short(card.home)}赢 ${high} 分`
}
</script>

<template>
  <section class="cards" aria-label="分析卡">
    <p class="cards-title"><span>分析卡</span>服务端按固定规则算出，文字解释在下面</p>
    <article v-for="card in cards" :key="card.event_id" class="card">
      <header class="card-head">
        <span class="card-match">{{ short(card.away) }} @ {{ short(card.home) }}</span>
        <span class="card-meta">{{ dateTime(card.commence_time) }}<template v-if="card.book"> · {{ card.book }}</template></span>
      </header>
      <div class="card-leans">
        <div v-if="card.spread" class="lean">
          <span class="lean-market">让分</span>
          <span class="lean-side">{{ spreadSide(card.spread) }}</span>
          <span class="lean-conf" :class="tone[card.spread.confidence]">{{ card.spread.confidence }}</span>
          <span class="lean-range">{{ margin(card, card.spread.expected) }}</span>
          <span class="lean-why">{{ card.spread.confidence_reason }}</span>
        </div>
        <div v-if="card.total" class="lean">
          <span class="lean-market">大小分</span>
          <span class="lean-side">{{ totalSide(card.total) }}</span>
          <span class="lean-conf" :class="tone[card.total.confidence]">{{ card.total.confidence }}</span>
          <span class="lean-range">总分 {{ card.total.expected[0] }}–{{ card.total.expected[1] }}</span>
          <span class="lean-why">{{ card.total.confidence_reason }}</span>
        </div>
      </div>
      <p v-if="card.missing.length" class="card-missing">缺失资料：{{ card.missing.join('、') }}</p>
    </article>
  </section>
</template>

<style scoped>
.cards { margin: 0 0 14px; }
.cards-title { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; font-size: 12px; color: var(--text-3); }
.cards-title span { padding: 1px 6px; border-radius: 1px; background: var(--cyan-soft); font-size: 11px; letter-spacing: .08em; color: var(--cyan); }
.card { margin-bottom: 8px; padding: 10px 14px; border: 1px solid var(--line); border-radius: var(--radius); background: rgba(5, 8, 11, .4); }
.card:last-child { margin-bottom: 0; }
.card-head { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; margin-bottom: 6px; }
.card-match { font-size: 14px; font-weight: 600; letter-spacing: .04em; }
.card-meta { font-size: 11px; font-variant-numeric: tabular-nums; color: var(--text-3); white-space: nowrap; }
.lean { display: grid; grid-template-columns: 52px minmax(96px, auto) 28px minmax(130px, auto) minmax(0, 1fr); align-items: baseline; gap: 10px; padding: 3px 0; font-size: 13px; }
.lean-market { font-size: 12px; color: var(--text-3); }
.lean-side { font-family: var(--font-num); font-weight: 600; font-variant-numeric: tabular-nums; white-space: nowrap; }
.lean-conf { justify-self: start; padding: 0 6px; border: 1px solid var(--line-2); border-radius: 1px; font-size: 12px; }
.lean-conf--high { border-color: var(--cyan-line); color: var(--cyan); }
.lean-conf--mid { border-color: var(--amber-line); color: var(--amber); }
.lean-conf--low { color: var(--text-3); }
.lean-range { font-variant-numeric: tabular-nums; white-space: nowrap; }
.lean-why { min-width: 0; font-size: 12px; color: var(--text-3); overflow-wrap: anywhere; }
.card-missing { margin: 6px 0 0; font-size: 12px; color: var(--amber); }
@media (max-width: 700px) {
  .lean { grid-template-columns: 44px auto 28px; row-gap: 2px; }
  .lean-range, .lean-why { grid-column: 2 / -1; }
}
</style>
