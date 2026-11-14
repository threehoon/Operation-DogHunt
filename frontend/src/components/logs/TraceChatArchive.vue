<script setup lang="ts">
// 聊天存档：这次提问实际发给模型的全部内容（对话、系统说明、读到的资料）和模型的回答，用来复现“为什么答成这样”。
import { computed, ref } from 'vue'

const props = defineProps<{ archive: Record<string, unknown> }>()
const showInstructions = ref(false)

const OUTCOME: Record<string, string> = { done: '完成', error: '出错', disconnected: '网页中途断开' }

const text = (key: string): string => (typeof props.archive[key] === 'string' ? (props.archive[key] as string) : '')
const messages = computed(() => {
  const request = props.archive.request
  const list = typeof request === 'object' && request !== null && 'messages' in request ? (request as { messages: unknown }).messages : []
  return Array.isArray(list) ? list.filter((m): m is { role: string; content: string } => typeof m?.role === 'string' && typeof m?.content === 'string') : []
})
const facts = computed(() => {
  const a = props.archive
  const usage = a.usage as Record<string, unknown> | null
  const rows: [string, string][] = [
    ['结果', OUTCOME[text('outcome')] ?? text('outcome')],
    ['模型', [text('model'), text('effort')].filter(Boolean).join(' · ')],
    ['开始', text('started_at')],
    ['结束', text('ended_at')],
    ['资料长度', a.context_chars != null ? `${a.context_chars} 字` : ''],
    ['用量', usage ? Object.entries(usage).map(([k, v]) => `${k}=${v}`).join(' ') : ''],
    ['提示词版本', a.prompt_versions ? JSON.stringify(a.prompt_versions) : ''],
    ['提示词锁定', a.prompt_locked == null ? '' : a.prompt_locked ? '与锁文件一致' : '与锁文件不一致'],
  ]
  return rows.filter(([, value]) => value)
})
const sources = computed(() => (props.archive.sources ? JSON.stringify(props.archive.sources, null, 2) : ''))
const selection = computed(() => (props.archive.selection ? JSON.stringify(props.archive.selection, null, 2) : ''))
</script>

<template>
  <div class="archive">
    <p v-if="text('error')" class="alert alert--bad">{{ text('error') }}</p>
    <dl class="facts">
      <div v-for="[label, value] in facts" :key="label"><dt>{{ label }}</dt><dd class="mono">{{ value }}</dd></div>
    </dl>

    <section>
      <h3 class="part-title">对话（{{ messages.length }} 条）</h3>
      <div v-for="(message, index) in messages" :key="index" class="message" :class="`message--${message.role}`">
        <span class="message-role">{{ message.role === 'user' ? '提问' : '回答' }}</span>
        <p>{{ message.content }}</p>
      </div>
    </section>

    <section>
      <h3 class="part-title">模型回答<small v-if="archive.reply_complete === false">（没有完整完成，只有中途收到的部分）</small></h3>
      <p v-if="text('reply')" class="reply">{{ text('reply') }}</p>
      <p v-else class="text-3">没有收到回答文字。</p>
    </section>

    <section v-if="selection || sources">
      <h3 class="part-title">读到的资料</h3>
      <pre v-if="selection" class="block mono">选中的比赛：{{ selection }}</pre>
      <pre v-if="sources" class="block mono">{{ sources }}</pre>
    </section>

    <section v-if="text('instructions')">
      <h3 class="part-title">
        系统说明（发给模型的完整资料，{{ text('instructions').length }} 字）
        <button type="button" class="btn btn--sm btn--quiet" :aria-expanded="showInstructions" @click="showInstructions = !showInstructions">{{ showInstructions ? '收起' : '展开' }}</button>
      </h3>
      <pre v-if="showInstructions" class="block block--tall mono">{{ text('instructions') }}</pre>
    </section>
  </div>
</template>

<style scoped>
.archive { display: grid; gap: 18px; }
.facts { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 18px; margin: 0; }
.facts div { min-width: 0; }
.facts dt { font-size: 11px; letter-spacing: .1em; color: var(--text-3); }
.facts dd { margin: 2px 0 0; font-size: 12px; color: var(--text); overflow-wrap: anywhere; }
.part-title { display: flex; align-items: center; gap: 10px; margin: 0 0 8px; font-size: 13px; font-weight: 500; color: var(--text); }
.part-title small { font-size: 12px; font-weight: 400; color: var(--amber); }
.message { display: grid; grid-template-columns: 40px minmax(0, 1fr); gap: 10px; padding: 8px 0; border-top: 1px solid var(--line); }
.message-role { font-size: 12px; color: var(--text-3); }
.message--user .message-role { color: var(--cyan); }
.message p, .reply { margin: 0; font-size: 13px; line-height: 1.75; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.block { max-height: 260px; margin: 0 0 8px; padding: 10px 12px; overflow: auto; border: 1px solid var(--line); border-radius: var(--radius); background: var(--well); font-size: 12px; line-height: 1.6; color: var(--text-2); white-space: pre-wrap; overflow-wrap: anywhere; }
.block--tall { max-height: 520px; }
</style>
