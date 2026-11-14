<script setup lang="ts">
// 调用日志：按调用编号查看每次请求经过了哪些步骤、在哪一步出错。只读本机的日志文件，不会重新发起任何调用。
import { computed, onBeforeUnmount, onDeactivated, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { getTrace, listTraces } from '@/api/logs'
import EmptyState from '@/components/common/EmptyState.vue'
import { KIND_LABEL, STATUS_LABEL, STATUS_TONE, copyText, duration } from '@/components/logs/labels'
import TraceChatArchive from '@/components/logs/TraceChatArchive.vue'
import TraceList from '@/components/logs/TraceList.vue'
import TraceTimeline from '@/components/logs/TraceTimeline.vue'
import type { TraceDetail, TraceKind, TraceSummary } from '@/types/logs'

const AUTO_MS = 5000
const route = useRoute()
const router = useRouter()

const days = ref(1)
const kind = ref<TraceKind | ''>('')
const problemsOnly = ref(false)
const search = ref('')
const items = ref<TraceSummary[]>([])
const total = ref(0)
const nextCursor = ref<number | null>(null)
const listError = ref('')
const loading = ref(false)
const loaded = ref(false)

const detail = ref<TraceDetail | null>(null)
const detailError = ref('')
const detailLoading = ref(false)
const tab = ref<'timeline' | 'archive'>('timeline')
const copied = ref('')
const auto = ref(false)
let timer = 0
let copiedTimer = 0

const selected = computed(() => (typeof route.query.trace === 'string' ? route.query.trace : null))

async function loadList(more = false): Promise<void> {
  loading.value = true
  try {
    const result = await listTraces({ days: days.value, kind: kind.value || null, problemsOnly: problemsOnly.value, q: search.value, cursor: more ? nextCursor.value ?? 0 : 0 })
    items.value = more ? [...items.value, ...result.items] : result.items
    total.value = result.total
    nextCursor.value = result.next_cursor
    listError.value = ''
  } catch (cause) {
    listError.value = cause instanceof Error ? cause.message : '读取调用日志失败。'
  } finally {
    loading.value = false
    loaded.value = true
  }
}

async function loadDetail(id: string, quiet = false): Promise<void> {
  if (!quiet) detailLoading.value = true
  try {
    const result = await getTrace(id)
    if (selected.value !== id) return
    if (!quiet && !result.archive) tab.value = 'timeline'
    detail.value = result
    detailError.value = ''
  } catch (cause) {
    if (selected.value !== id) return
    if (!quiet) detail.value = null
    detailError.value = cause instanceof Error ? cause.message : '读取调用详情失败。'
  } finally {
    detailLoading.value = false
  }
}

function select(id: string): void {
  void router.replace({ query: { ...route.query, trace: id } })
}

function refresh(): void {
  void loadList()
  if (selected.value) void loadDetail(selected.value, true)
}

watch([days, kind, problemsOnly], () => void loadList(), { immediate: true })
let searchTimer = 0
watch(search, () => {
  window.clearTimeout(searchTimer)
  searchTimer = window.setTimeout(() => void loadList(), 300)
})
watch(selected, (id) => {
  detail.value = null
  detailError.value = ''
  tab.value = 'timeline'
  if (id) void loadDetail(id)
}, { immediate: true })

function stopAuto(): void {
  window.clearInterval(timer)
  timer = 0
}
watch(auto, (on) => {
  stopAuto()
  // 标签页在后台时不刷新。
  if (on) timer = window.setInterval(() => { if (!document.hidden) refresh() }, AUTO_MS)
})
onDeactivated(() => { auto.value = false })
onBeforeUnmount(() => {
  stopAuto()
  window.clearTimeout(searchTimer)
  window.clearTimeout(copiedTimer)
})

async function copy(label: string, text: string): Promise<void> {
  copied.value = (await copyText(text)) ? `已复制${label}` : '浏览器不允许复制，请手动选择文字'
  window.clearTimeout(copiedTimer)
  copiedTimer = window.setTimeout(() => { copied.value = '' }, 2400)
}

const summary = computed(() => detail.value?.summary ?? null)
</script>

<template>
  <section class="page logs" aria-labelledby="logs-heading">
    <header class="page-head reveal">
      <div>
        <p class="overline"><b>LOG</b>维修间</p>
        <h1 id="logs-heading" class="page-title">调用日志</h1>
        <p class="page-sub">每次请求都有一个调用编号。出问题时按编号看它经过了哪些步骤、停在哪一步，不用重新跑一遍。日志保留 14 天，聊天存档保留最近 200 次，密钥已脱敏。</p>
      </div>
      <div class="page-actions">
        <label class="auto"><input v-model="auto" type="checkbox" /> 每 5 秒自动刷新</label>
        <button type="button" class="btn btn--sm" :disabled="loading" @click="refresh">刷新</button>
      </div>
    </header>

    <div class="toolbar reveal" style="--i: 1">
      <div class="seg" role="group" aria-label="时间范围">
        <button v-for="option in [[1, '今天'], [3, '3 天'], [14, '14 天']] as const" :key="option[0]" type="button" :aria-pressed="days === option[0]" @click="days = option[0]">{{ option[1] }}</button>
      </div>
      <select v-model="kind" class="select" aria-label="调用类型">
        <option value="">全部类型</option>
        <option v-for="(label, key) in KIND_LABEL" :key="key" :value="key">{{ label }}</option>
      </select>
      <label class="auto"><input v-model="problemsOnly" type="checkbox" /> 只看有问题的</label>
      <input v-model="search" class="input search" type="search" maxlength="100" placeholder="调用编号、接口或问题说明" aria-label="搜索调用" />
      <span class="toolbar-count num" role="status">{{ items.length }} / {{ total }} 次调用</span>
    </div>

    <p v-if="listError" class="alert alert--bad reveal" role="alert">{{ listError }}</p>

    <div class="logs-body reveal" style="--i: 2">
      <div class="list-col">
        <TraceList v-if="items.length" :items="items" :selected="selected" @select="select" />
        <EmptyState v-else-if="loaded && !listError" title="这段时间没有调用记录">
          <p>后端启动后的每次请求都会记在这里。日志页自己的请求不记。换个时间范围或去掉筛选再看看。</p>
        </EmptyState>
        <button v-if="nextCursor != null" type="button" class="btn btn--sm more" :disabled="loading" @click="loadList(true)">再加载 50 条</button>
      </div>

      <div class="detail-col panel panel-pad">
        <p v-if="!selected" class="text-3 hint">在左侧选一次调用查看详情；页面上的报错提示也会附上编号链接到这里。</p>
        <p v-else-if="detailLoading" class="text-3 hint" role="status">正在读取 {{ selected }}…</p>
        <p v-else-if="detailError" class="alert alert--bad" role="alert">{{ detailError }}</p>
        <template v-if="summary && detail">
          <header class="detail-head">
            <div class="detail-title">
              <span class="tag" :class="STATUS_TONE[summary.status]">{{ STATUS_LABEL[summary.status] }}</span>
              <span class="text-3">{{ KIND_LABEL[summary.kind] }}</span>
              <span class="mono">{{ summary.title }}</span>
            </div>
            <div class="detail-actions">
              <span v-if="copied" class="text-3 copied" role="status">{{ copied }}</span>
              <button type="button" class="btn btn--sm" @click="copy('编号', summary.trace_id)">复制编号</button>
              <button type="button" class="btn btn--sm" @click="copy(' JSON', JSON.stringify(detail, null, 2))">复制 JSON</button>
            </div>
          </header>
          <dl class="detail-facts num">
            <div><dt>编号</dt><dd class="mono">{{ summary.trace_id }}</dd></div>
            <div><dt>开始</dt><dd class="mono">{{ summary.started_at }}</dd></div>
            <div><dt>耗时</dt><dd>{{ duration(summary.duration_ms) }}</dd></div>
            <div><dt>步骤</dt><dd>{{ summary.steps }} 条 · 警告 {{ summary.warnings }} · 错误 {{ summary.errors }}</dd></div>
            <div v-if="summary.parent"><dt>由谁触发</dt><dd><button type="button" class="link mono" @click="select(summary.parent!)">{{ summary.parent }}</button></dd></div>
          </dl>
          <p v-if="summary.headline" class="alert" :class="summary.status === 'error' ? 'alert--bad' : 'alert--warn'">{{ summary.headline }}</p>

          <div v-if="detail.children.length" class="children">
            <span class="label">触发的后台任务</span>
            <button v-for="child in detail.children" :key="child.trace_id" type="button" class="child" @click="select(child.trace_id)">
              <span class="tag" :class="STATUS_TONE[child.status]">{{ STATUS_LABEL[child.status] }}</span>
              <span class="mono">{{ child.trace_id }}</span>
              <span class="text-3">{{ duration(child.duration_ms) }}</span>
              <span v-if="child.headline" class="child-headline">{{ child.headline }}</span>
            </button>
          </div>

          <div class="seg tabs" role="tablist" aria-label="详情内容">
            <button type="button" role="tab" :aria-selected="tab === 'timeline'" :aria-pressed="tab === 'timeline'" @click="tab = 'timeline'">时间线<span class="seg-count">{{ detail.entries.length }}</span></button>
            <button type="button" role="tab" :aria-selected="tab === 'archive'" :aria-pressed="tab === 'archive'" :disabled="!detail.archive" :title="detail.archive ? '' : '只有聊天会存档完整输入与回答，且只保留最近 200 次'" @click="tab = 'archive'">聊天存档</button>
          </div>
          <TraceTimeline v-if="tab === 'timeline'" :entries="detail.entries" />
          <TraceChatArchive v-else-if="detail.archive" :archive="detail.archive" />
        </template>
      </div>
    </div>
  </section>
</template>

<style scoped>
.toolbar { display: flex; align-items: center; gap: 10px 14px; flex-wrap: wrap; margin: 24px 0 16px; }
.search { flex: 1 1 220px; min-width: 0; }
.toolbar-count { margin-left: auto; font-size: 12px; color: var(--text-3); }
.auto { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; color: var(--text-2); cursor: pointer; }
.auto input { accent-color: var(--cyan); }
.logs-body { display: grid; grid-template-columns: minmax(300px, 5fr) minmax(0, 7fr); gap: 18px; align-items: start; }
.list-col { display: grid; gap: 10px; min-width: 0; }
.more { justify-self: center; }
.detail-col { position: sticky; top: calc(var(--strip-height) + 16px); display: grid; gap: 14px; min-width: 0; max-height: calc(100dvh - var(--strip-height) - 32px); overflow: auto; }
.hint { margin: 0; font-size: 13px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
.detail-title { display: flex; align-items: center; gap: 8px; min-width: 0; font-size: 13px; overflow-wrap: anywhere; }
.detail-actions { display: flex; align-items: center; gap: 6px; }
.copied { font-size: 12px; }
.detail-facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(170px, 1fr)); gap: 8px 16px; margin: 0; }
.detail-facts dt { font-size: 11px; letter-spacing: .1em; color: var(--text-3); }
.detail-facts dd { margin: 2px 0 0; font-size: 12px; color: var(--text); overflow-wrap: anywhere; }
.detail-facts .link { padding: 0; border: 0; background: none; font-size: 12px; }
.children { display: grid; gap: 6px; }
.child { display: flex; align-items: center; gap: 10px; min-width: 0; padding: 7px 10px; border: 1px solid var(--line); border-radius: var(--radius); background: transparent; color: var(--text-2); font-size: 12px; text-align: left; }
.child:hover { border-color: var(--line-3); background: rgba(160, 190, 202, .05); }
.child-headline { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.tabs { justify-self: start; }
@media (max-width: 1100px) {
  .logs-body { grid-template-columns: minmax(0, 1fr); }
  .detail-col { position: static; max-height: none; }
}
</style>
