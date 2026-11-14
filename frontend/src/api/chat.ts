import { getJson, postStream, TraceError } from './client'
import type { AnalysisCard, ChatMessage, ChatResponse, ModelStatus } from '@/types/api'

export async function getModelStatus(): Promise<ModelStatus> {
  const result = await getJson('/model')
  if (
    typeof result !== 'object' || result === null ||
    !('configured' in result) || typeof result.configured !== 'boolean' ||
    !('model' in result) || (typeof result.model !== 'string' && result.model !== null) ||
    !('timeout_seconds' in result) || typeof result.timeout_seconds !== 'number' ||
    result.timeout_seconds < 5 || result.timeout_seconds > 300
  ) throw new Error('模型配置状态格式不正确，请检查后端。')
  return result as ModelStatus
}

function isChatResponse(result: unknown): result is ChatResponse {
  return typeof result === 'object' && result !== null &&
    'reply' in result && typeof result.reply === 'string' && !!result.reply.trim() &&
    'model' in result && typeof result.model === 'string'
}

function isLean(value: unknown): boolean {
  return value === null || (typeof value === 'object' && 'side' in value && typeof value.side === 'string' &&
    'confidence' in value && typeof value.confidence === 'string' &&
    'expected' in value && Array.isArray(value.expected) && value.expected.length === 2)
}

/** analysis 事件（或存档里的分析卡）格式不对时整份丢弃，不影响文字回答。 */
export function analysisCards(data: unknown): AnalysisCard[] | null {
  if (typeof data !== 'object' || data === null || !('cards' in data) || !Array.isArray(data.cards)) return null
  const ok = data.cards.every((card: unknown) => typeof card === 'object' && card !== null &&
    'event_id' in card && typeof card.event_id === 'string' && 'home' in card && typeof card.home === 'string' &&
    'away' in card && typeof card.away === 'string' && 'spread' in card && isLean(card.spread) && 'total' in card && isLean(card.total))
  return ok ? data.cards as AnalysisCard[] : null
}

/**
 * 逐段接收回答：每段文字交给 onText（用于边收边显示），完整完成后返回最终回答。
 * 服务端在文字之前先发 analysis（每场一张分析卡），交给 onAnalysis。
 * 中途失败会抛错，已显示的文字和分析卡不算交付，调用方应丢弃。
 */
export async function streamChat(messages: ChatMessage[], timeoutSeconds: number, oddsSelection: { event_id: string; bookmaker_key: string } | null, onText: (text: string) => void, onAnalysis: (cards: AnalysisCard[]) => void = () => {}): Promise<ChatResponse> {
  let final: ChatResponse | null = null
  let failure = ''
  const trace = await postStream('/chat/stream', { messages, odds_selection: oddsSelection }, (timeoutSeconds + 10) * 1000, ({ event, data }) => {
    if (event === 'analysis') {
      const cards = analysisCards(data)
      if (cards) onAnalysis(cards)
    } else if (event === 'delta' && typeof data === 'object' && data !== null && 'text' in data && typeof data.text === 'string') onText(data.text)
    else if (event === 'done' && isChatResponse(data)) final = data
    else if (event === 'error') failure = typeof data === 'object' && data !== null && 'detail' in data && typeof data.detail === 'string' ? data.detail : '模型回答失败，本次未交付结果，请手动重试。'
  })
  if (failure) throw new TraceError(failure, trace)
  if (!final) throw new TraceError('与后端的连接提前结束，没有收到完整回答，请手动重试。', trace)
  return final
}
