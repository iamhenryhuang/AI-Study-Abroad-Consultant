export type AgentEvent =
  | { type: 'thinking'; step: number | 'extension_function' }
  | { type: 'tool_call'; tool: string; args: Record<string, string> }
  | { type: 'tool_result'; tool: string; preview: string }
  | { type: 'llm_call'; purpose: 'planner' | 'finalizer'; round?: number }
  | { type: 'answer'; text: string }
  | { type: 'error'; message: string }

export interface Message {
  id: string
  role: 'user' | 'assistant'
  text: string
  events: AgentEvent[]
  loading: boolean
  error?: boolean
}

export interface ChatSession {
  id: string
  title: string
  updatedAt: number
  messages: Message[]
}
