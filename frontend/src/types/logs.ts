// 调用日志页的数据形状，对应后端 /api/v1/logs/traces（见 backend/app/services/logs/reader.py）。

export type TraceKind = 'chat' | 'odds_refresh' | 'task' | 'api'
export type TraceStatus = 'running' | 'error' | 'warning' | 'ok'
export type LogLevel = 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'

/** 一次调用的概要：同一个调用编号下的全部日志归成一条。 */
export interface TraceSummary {
  trace_id: string
  /** 后台任务记着是哪次调用触发的。 */
  parent: string | null
  kind: TraceKind
  title: string
  status: TraceStatus
  http_status: number | null
  started_at: string
  ended_at: string
  duration_ms: number | null
  steps: number
  warnings: number
  errors: number
  /** 第一条警告或错误的说明。 */
  headline: string | null
  /** 这次调用触发的后台任务编号。 */
  children: string[]
}

export interface TraceList {
  items: TraceSummary[]
  total: number
  next_cursor: number | null
}

/** 出错位置只列本项目代码的帧，最深的在最后；message 只有项目自己的错误类型才记。 */
export interface LogError {
  type: string
  where: string[]
  message?: string
  cause?: { type: string; where: string[] }
}

export interface LogEntry {
  ts: string
  level: LogLevel
  trace_id: string | null
  parent: string | null
  logger: string
  event: string
  msg: string
  data?: Record<string, unknown>
  error?: LogError
}

export interface TraceDetail {
  summary: TraceSummary
  entries: LogEntry[]
  children: TraceSummary[]
  /** 聊天的完整输入与回答，只保留最近 200 次；其他调用为 null。 */
  archive: Record<string, unknown> | null
}
