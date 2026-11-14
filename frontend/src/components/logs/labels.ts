// 调用日志页的中文名称与显示格式。事件名在后端固定（见 backend/app/core/applog.py 的说明）。
import type { LogLevel, TraceKind, TraceStatus } from '@/types/logs'

export const EVENT_LABEL: Record<string, string> = {
  'http.request': '接口请求',
  'chat.context': '组装资料',
  'chat.end': '聊天结束',
  'chat.rejected': '聊天被拒',
  'chat.crash': '聊天意外中断',
  'llm.start': '请求模型',
  'llm.first_token': '收到首字',
  'llm.done': '模型完成',
  'llm.http': '模型接口报错',
  'llm.error': '模型失败',
  'ext.call': '外部接口',
  'task.start': '后台开始',
  'task.step': '后台步骤',
  'task.end': '后台结束',
  'task.error': '取数失败',
  'odds.saved': '盘口已保存',
  'odds.error': '盘口保存失败',
  'archive.error': '存档失败',
  log: '日志',
}

export const KIND_LABEL: Record<TraceKind, string> = {
  chat: '聊天',
  odds_refresh: '更新盘口',
  task: '后台任务',
  api: '其他接口',
}

export const STATUS_LABEL: Record<TraceStatus, string> = {
  running: '进行中',
  error: '出错',
  warning: '有警告',
  ok: '正常',
}

export const STATUS_TONE: Record<TraceStatus, string> = {
  running: 'tag--info',
  error: 'tag--bad',
  warning: 'tag--warn',
  ok: 'tag--ok',
}

export const LEVEL_TONE: Record<LogLevel, string> = {
  DEBUG: '',
  INFO: '',
  WARNING: 'warn',
  ERROR: 'bad',
  CRITICAL: 'bad',
}

export function eventLabel(name: string): string {
  return EVENT_LABEL[name] ?? name
}

/** 后端时间是带 +08:00 的北京时间，直接截取，不经浏览器时区换算。 */
export function clock(ts: string): string {
  return ts.slice(11, 23)
}

export function day(ts: string): string {
  return ts.slice(5, 10)
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return '—'
  if (ms < 1000) return `${ms} ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`
  return `${Math.floor(ms / 60000)} 分 ${Math.round((ms % 60000) / 1000)} 秒`
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
