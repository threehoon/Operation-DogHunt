/** 所有前端 HTTP 请求集中在这里；不读取任何后端密钥。 */

/** 带后端调用编号（响应头 X-Trace-Id）的错误；页面据此链接到“调用日志”。连接没到后端时没有编号。 */
export class TraceError extends Error {
  constructor(message: string, readonly traceId: string | null) {
    super(message)
    this.name = 'TraceError'
  }
}

/** 从任意错误里取调用编号；没有时返回 null。 */
export function traceOf(error: unknown): string | null {
  return error instanceof TraceError ? error.traceId : null
}

export function traceHeader(response: Response): string | null {
  const value = response.headers.get('x-trace-id')
  return value && /^(task-)?[0-9a-f]{12}$/.test(value) ? value : null
}

export async function getJson(path: string): Promise<unknown> {
  return requestJson(path)
}

export async function postJson(path: string, body: unknown, timeoutMs: number): Promise<unknown> {
  return requestJson(path, { method: 'POST', body: JSON.stringify(body) }, timeoutMs)
}

async function requestJson(path: string, options: RequestInit = {}, timeoutMs = 5000): Promise<unknown> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`/api/v1${path}`, {
      ...options,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}) },
      cache: 'no-store',
    })
    if (!response.ok) {
      const trace = traceHeader(response)
      const body: unknown = await response.json().catch(() => null)
      if (typeof body === 'object' && body !== null && 'detail' in body) {
        if (typeof body.detail === 'string') throw new TraceError(body.detail, trace)
        if (response.status === 422) throw new TraceError('输入或聊天上下文超出限制，请缩短问题或开始新对话。', trace)
      }
      throw new TraceError(`后端暂不可用（HTTP ${response.status}）`, trace)
    }
    return await response.json()
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(options.method === 'POST' ? '等待回答超时，未自动重试。中转站可能已经计费，请稍后手动重试。' : '连接后端超时，请确认后端正在运行。')
    }
    if (error instanceof TypeError) {
      throw new Error('无法连接后端，请确认后端正在运行。')
    }
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}

export type StreamEvent = { event: string; data: unknown }

/**
 * POST 并逐个读取后端 SSE 事件（event/data 两行一组）。开始推送前的错误按 HTTP 状态处理，
 * 之后的错误由后端以 error 事件给出。超时从发出请求算起，覆盖整个回答。
 */
export async function postStream(path: string, body: unknown, timeoutMs: number, onEvent: (item: StreamEvent) => void): Promise<string | null> {
  const controller = new AbortController()
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs)
  let trace: string | null = null
  try {
    const response = await fetch(`/api/v1${path}`, {
      method: 'POST',
      body: JSON.stringify(body),
      signal: controller.signal,
      headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
      cache: 'no-store',
    })
    trace = traceHeader(response)
    if (!response.ok) {
      const detail: unknown = await response.json().catch(() => null)
      if (typeof detail === 'object' && detail !== null && 'detail' in detail) {
        if (typeof detail.detail === 'string') throw new TraceError(detail.detail, trace)
        if (response.status === 422) throw new TraceError('输入或聊天上下文超出限制，请缩短问题或开始新对话。', trace)
      }
      throw new TraceError(`后端暂不可用（HTTP ${response.status}）`, trace)
    }
    if (!response.body) throw new Error('浏览器不支持逐段读取回答。')
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += value
      let end: number
      while ((end = buffer.indexOf('\n\n')) >= 0) {
        const block = buffer.slice(0, end)
        buffer = buffer.slice(end + 2)
        let event = 'message'
        const data: string[] = []
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
        }
        if (data.length) onEvent({ event, data: JSON.parse(data.join('\n')) })
      }
    }
    return trace
  } catch (error) {
    if (controller.signal.aborted) throw new TraceError('等待回答超时，未自动重试。中转站可能已经计费，请稍后手动重试。', trace)
    if (error instanceof TypeError) throw new TraceError(trace ? '与后端的连接中断，本次回答未完成，请手动重试。' : '无法连接后端，请确认后端正在运行。', trace)
    if (error instanceof SyntaxError) throw new TraceError('后端推送的数据格式不正确，本次回答未完成。', trace)
    throw error
  } finally {
    window.clearTimeout(timeout)
  }
}
