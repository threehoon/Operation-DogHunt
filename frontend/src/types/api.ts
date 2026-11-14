export interface HealthResponse {
  status: 'ok'
  app: string
  environment: 'development' | 'test' | 'production'
  version: string
}

export interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
}

export interface ModelStatus {
  configured: boolean
  model: string | null
  timeout_seconds: number
}

/** 服务端算好的一个玩法的倾向（match_analysis_v1），聊天流在文字之前发出。 */
export interface AnalysisLean {
  market: 'spread' | 'total'
  /** 球队英文全名，或 Over / Under。 */
  side: string
  /** 这一边的让分点（球队视角）或总分线。 */
  point: number | null
  book: string | null
  confidence: '高' | '中' | '低'
  confidence_reason: string
  weak: boolean
  /** 让分：主队净胜分范围；大小分：总分范围。 */
  expected: [number, number]
}

export interface AnalysisCard {
  event_id: string
  home: string
  away: string
  commence_time: string
  book: string | null
  spread: AnalysisLean | null
  total: AnalysisLean | null
  missing: string[]
  version: string
}

export interface ChatResponse {
  reply: string
  model: string
  usage?: { input_tokens: number; output_tokens: number } | null
}
