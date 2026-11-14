<script setup lang="ts">
// 调用列表：一行一次调用，新的在上；出错和警告的行左侧有色条，并显示第一条问题的说明。
import type { TraceSummary } from '@/types/logs'
import { KIND_LABEL, STATUS_LABEL, STATUS_TONE, clock, day, duration } from './labels'

defineProps<{ items: TraceSummary[]; selected: string | null }>()
const emit = defineEmits<{ select: [id: string] }>()
</script>

<template>
  <ul class="trace-list" aria-label="调用列表">
    <li v-for="item in items" :key="item.trace_id">
      <button type="button" class="trace-row" :class="`trace-row--${item.status}`" :aria-current="item.trace_id === selected ? 'true' : undefined" @click="emit('select', item.trace_id)">
        <span class="row-top">
          <span class="tag" :class="STATUS_TONE[item.status]">{{ STATUS_LABEL[item.status] }}</span>
          <span class="row-kind">{{ KIND_LABEL[item.kind] }}</span>
          <span class="row-title mono">{{ item.title }}</span>
          <span class="row-time num" :title="item.started_at">{{ day(item.started_at) }} {{ clock(item.started_at).slice(0, 8) }}</span>
        </span>
        <span v-if="item.headline" class="row-headline">{{ item.headline }}</span>
        <span class="row-meta num">
          <span class="mono">{{ item.trace_id }}</span>
          <span>{{ duration(item.duration_ms) }}</span>
          <span>{{ item.steps }} 条</span>
          <span v-if="item.http_status" :class="{ neg: item.http_status >= 400 }">HTTP {{ item.http_status }}</span>
          <span v-if="item.children.length">触发 {{ item.children.length }} 个后台任务</span>
          <span v-if="item.parent">由 {{ item.parent }} 触发</span>
        </span>
      </button>
    </li>
  </ul>
</template>

<style scoped>
.trace-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.trace-row {
  display: grid; gap: 5px; width: 100%; padding: 10px 12px 10px 14px; border: 1px solid var(--line); border-left: 2px solid var(--line-2);
  border-radius: var(--radius); background: transparent; color: var(--text-2); text-align: left; transition: background .14s, border-color .14s;
}
.trace-row:hover { background: rgba(160, 190, 202, .05); border-color: var(--line-2); }
.trace-row[aria-current='true'] { background: var(--cyan-soft); border-color: var(--cyan-line); color: var(--text); }
.trace-row--error { border-left-color: var(--red); }
.trace-row--warning { border-left-color: var(--amber); }
.trace-row--running { border-left-color: var(--cyan); }
.row-top { display: flex; align-items: center; gap: 8px; min-width: 0; }
.row-kind { flex: 0 0 auto; font-size: 12px; color: var(--text-3); }
.row-title { flex: 1; min-width: 0; overflow: hidden; font-size: 12px; color: var(--text); text-overflow: ellipsis; white-space: nowrap; }
.row-time { flex: 0 0 auto; font-size: 12px; color: var(--text-3); }
.row-headline { overflow: hidden; font-size: 13px; color: var(--text); text-overflow: ellipsis; white-space: nowrap; }
.row-meta { display: flex; flex-wrap: wrap; gap: 4px 12px; font-size: 11px; color: var(--text-3); }
</style>
