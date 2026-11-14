<script setup lang="ts">
// 一次调用的时间线：每一步离开始过了多久、是哪一步、结果怎样。出错的步骤默认展开，显示错误类型和出错的代码位置。
import { computed, ref, watch } from 'vue'
import type { LogEntry } from '@/types/logs'
import { LEVEL_TONE, clock, eventLabel } from './labels'

const props = defineProps<{ entries: LogEntry[] }>()

const start = computed(() => (props.entries.length ? Date.parse(props.entries[0].ts) : 0))
const open = ref(new Set<number>())

// 换一次调用时，默认展开所有带错误的步骤。
watch(() => props.entries, (entries) => {
  open.value = new Set(entries.flatMap((entry, index) => (entry.error ? [index] : [])))
}, { immediate: true })

function toggle(index: number): void {
  const next = new Set(open.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  open.value = next
}

/** 时间线上直接露出的几个字段；其余的展开后看。 */
const BRIEF = ['ms', 'status', 'step', 'provider', 'op', 'model', 'outcome', 'rows', 'credits', 'first_token_s', 'total_s', 'output_tokens']

function brief(entry: LogEntry): [string, string][] {
  const data = entry.data ?? {}
  return BRIEF.filter((key) => data[key] != null && typeof data[key] !== 'object').map((key) => [key, String(data[key])])
}

function expandable(entry: LogEntry): boolean {
  return !!entry.error || Object.keys(entry.data ?? {}).length > 0
}
</script>

<template>
  <ol class="timeline" aria-label="调用时间线">
    <li v-for="(entry, index) in entries" :key="index" class="step" :class="LEVEL_TONE[entry.level] && `step--${LEVEL_TONE[entry.level]}`">
      <span class="step-offset num" :title="clock(entry.ts)">+{{ Math.max(0, Date.parse(entry.ts) - start) }} ms</span>
      <span class="step-dot" aria-hidden="true"></span>
      <div class="step-body">
        <button type="button" class="step-head" :disabled="!expandable(entry)" :aria-expanded="expandable(entry) ? open.has(index) : undefined" @click="toggle(index)">
          <span class="step-name">{{ eventLabel(entry.event) }}</span>
          <span v-if="entry.level !== 'INFO'" class="step-level mono">{{ entry.level }}</span>
          <span class="step-msg">{{ entry.msg }}</span>
          <span v-if="expandable(entry)" class="step-toggle" aria-hidden="true">{{ open.has(index) ? '收起' : '详情' }}</span>
        </button>
        <span v-if="brief(entry).length" class="step-brief mono">
          <span v-for="[key, value] in brief(entry)" :key="key">{{ key }}={{ value }}</span>
        </span>
        <div v-if="open.has(index)" class="step-detail">
          <div v-if="entry.error" class="step-error">
            <p><b class="mono">{{ entry.error.type }}</b><template v-if="entry.error.message">：{{ entry.error.message }}</template></p>
            <p v-if="!entry.error.message" class="text-3">这类错误的原文可能含外部返回内容，日志里只记类型和位置。</p>
            <ol v-if="entry.error.where.length" class="where mono" aria-label="出错位置，最深的在最后">
              <li v-for="frame in entry.error.where" :key="frame">{{ frame }}</li>
            </ol>
            <template v-if="entry.error.cause">
              <p class="text-3">原始错误：<span class="mono">{{ entry.error.cause.type }}</span></p>
              <ol v-if="entry.error.cause.where.length" class="where mono">
                <li v-for="frame in entry.error.cause.where" :key="frame">{{ frame }}</li>
              </ol>
            </template>
          </div>
          <pre v-if="entry.data && Object.keys(entry.data).length" class="step-data mono">{{ JSON.stringify(entry.data, null, 2) }}</pre>
          <p class="step-source text-3 mono">{{ clock(entry.ts) }} · {{ entry.logger }}</p>
        </div>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.timeline { margin: 0; padding: 0; list-style: none; }
.step { display: grid; grid-template-columns: 76px 14px minmax(0, 1fr); gap: 0 10px; }
.step-offset { padding-top: 7px; font-size: 11px; color: var(--text-3); text-align: right; white-space: nowrap; }
.step-dot { position: relative; }
.step-dot::before { content: ''; position: absolute; top: 0; bottom: 0; left: 6px; width: 1px; background: var(--line-2); }
.step-dot::after { content: ''; position: absolute; top: 11px; left: 3px; width: 7px; height: 7px; border-radius: 50%; background: var(--text-3); }
.step:first-child .step-dot::before { top: 12px; }
.step:last-child .step-dot::before { bottom: auto; height: 12px; }
.step--warn .step-dot::after { background: var(--amber); }
.step--bad .step-dot::after { background: var(--red); }
.step-body { min-width: 0; padding: 3px 0 10px; }
.step-head { display: flex; align-items: baseline; gap: 8px; width: 100%; padding: 3px 0; border: 0; background: none; color: var(--text-2); text-align: left; }
.step-head:not(:disabled):hover .step-msg { color: var(--text); }
.step-head:disabled { cursor: default; opacity: 1; }
.step-name { flex: 0 0 auto; font-size: 13px; color: var(--text); }
.step--warn .step-name, .step--warn .step-level { color: var(--amber); }
.step--bad .step-name, .step--bad .step-level { color: var(--red); }
.step-level { flex: 0 0 auto; font-size: 11px; }
.step-msg { flex: 1; min-width: 0; font-size: 13px; overflow-wrap: anywhere; }
.step-toggle { flex: 0 0 auto; font-size: 12px; color: var(--cyan); }
.step-brief { display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 11px; color: var(--text-3); }
.step-detail { display: grid; gap: 8px; margin-top: 8px; padding: 10px 12px; border: 1px solid var(--line); border-radius: var(--radius); background: var(--well); }
.step-error p { margin: 0; font-size: 13px; line-height: 1.7; }
.step--bad .step-error b { color: var(--red); }
.where { margin: 4px 0 0; padding-left: 18px; font-size: 12px; line-height: 1.7; color: var(--text-2); }
.step-data { max-height: 280px; margin: 0; overflow: auto; font-size: 12px; line-height: 1.6; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.step-source { margin: 0; font-size: 11px; }
</style>
