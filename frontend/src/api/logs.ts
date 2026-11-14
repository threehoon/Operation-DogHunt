import { getJson } from './client'
import type { LogEntry, TraceDetail, TraceKind, TraceList, TraceSummary } from '@/types/logs'

const STATUSES = ['running', 'error', 'warning', 'ok']
const KINDS = ['chat', 'odds_refresh', 'task', 'api']

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isSummary(value: unknown): value is TraceSummary {
  return isObject(value) && typeof value.trace_id === 'string' && typeof value.title === 'string'
    && typeof value.started_at === 'string' && typeof value.steps === 'number'
    && STATUSES.includes(value.status as string) && KINDS.includes(value.kind as string)
    && Array.isArray(value.children)
}

function isEntry(value: unknown): value is LogEntry {
  return isObject(value) && typeof value.ts === 'string' && typeof value.level === 'string'
    && typeof value.event === 'string' && typeof value.msg === 'string'
}

export interface TraceQuery {
  days: number
  kind: TraceKind | null
  problemsOnly: boolean
  q: string
  cursor?: number
}

export async function listTraces(query: TraceQuery): Promise<TraceList> {
  const params = new URLSearchParams({ days: String(query.days), limit: '50', cursor: String(query.cursor ?? 0) })
  if (query.kind) params.set('kind', query.kind)
  if (query.problemsOnly) params.set('problems_only', 'true')
  if (query.q.trim()) params.set('q', query.q.trim().slice(0, 100))
  const result = await getJson(`/logs/traces?${params}`)
  if (!isObject(result) || !Array.isArray(result.items) || !result.items.every(isSummary) || typeof result.total !== 'number') {
    throw new Error('调用日志列表格式不正确，请检查后端。')
  }
  return result as unknown as TraceList
}

export async function getTrace(traceId: string): Promise<TraceDetail> {
  const result = await getJson(`/logs/traces/${encodeURIComponent(traceId)}`)
  if (!isObject(result) || !isSummary(result.summary) || !Array.isArray(result.entries) || !result.entries.every(isEntry)
    || !Array.isArray(result.children) || !result.children.every(isSummary) || !(result.archive === null || isObject(result.archive))) {
    throw new Error('调用详情格式不正确，请检查后端。')
  }
  return result as unknown as TraceDetail
}
