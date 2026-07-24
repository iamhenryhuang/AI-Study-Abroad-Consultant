import { useEffect, useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import type { AgentEvent, ChatSession, Message } from '../types'

const STORAGE_KEY = 'study_abroad_chat_sessions'

export function useStreamChat() {
  const [sessions, setSessions] = useState<ChatSession[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (!saved) return []
      const parsed: ChatSession[] = JSON.parse(saved)
      return parsed.map((session) => ({
        ...session,
        messages: session.messages.map((message) => ({
          ...message,
          events: message.events || [],
        })),
      }))
    } catch {
      return []
    }
  })

  const [currentSessionId, setCurrentSessionId] = useState<string | null>(
    sessions[0]?.id || null,
  )

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
  }, [sessions])

  const currentSession = sessions.find((session) => session.id === currentSessionId)
  const messages = currentSession?.messages || []

  const getOrCreateSession = (firstMessageText: string) => {
    if (currentSessionId && sessions.some((session) => session.id === currentSessionId)) {
      return currentSessionId
    }

    const newSessionId = crypto.randomUUID()
    const title =
      firstMessageText.length > 20 ? `${firstMessageText.slice(0, 20)}...` : firstMessageText

    const newSession: ChatSession = {
      id: newSessionId,
      title,
      updatedAt: Date.now(),
      messages: [],
    }

    setSessions((previous) => [newSession, ...previous])
    setCurrentSessionId(newSessionId)
    return newSessionId
  }

  const mutation = useMutation({
    mutationFn: async (query: string) => {
      const targetSessionId = getOrCreateSession(query)
      const assistantId = crypto.randomUUID()
      const userMessage: Message = {
        id: crypto.randomUUID(),
        role: 'user',
        text: query,
        events: [],
        loading: false,
      }
      const assistantMessage: Message = {
        id: assistantId,
        role: 'assistant',
        text: '',
        events: [],
        loading: true,
      }

      setSessions((previous) =>
        previous.map((session) => {
          if (session.id !== targetSessionId) return session
          return {
            ...session,
            messages: [...session.messages, userMessage, assistantMessage],
            updatedAt: Date.now(),
          }
        }),
      )

      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })

      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      if (!response.body) throw new Error('Response body is empty')

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() ?? ''

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue

          try {
            const event: AgentEvent = JSON.parse(line.slice(6))
            setSessions((previous) =>
              previous.map((session) => {
                if (session.id !== targetSessionId) return session
                return {
                  ...session,
                  messages: session.messages.map((message) => {
                    if (message.id !== assistantId) return message
                    if (event.type === 'answer_chunk') {
                      return { ...message, text: message.text + event.text }
                    }

                    const base = { ...message, events: [...message.events, event] }
                    if (event.type === 'answer') {
                      return { ...base, text: event.text, loading: false }
                    }
                    if (event.type === 'error') {
                      return { ...base, text: event.message, loading: false, error: true }
                    }
                    return base
                  }),
                }
              }),
            )
          } catch {
            // Ignore malformed SSE payloads.
          }
        }
      }
    },

    onError: (error) => {
      setSessions((previous) =>
        previous.map((session) => {
          if (session.id !== currentSessionId) return session
          const lastLoadingAssistant = [...session.messages]
            .reverse()
            .find((message) => message.role === 'assistant' && message.loading)

          if (!lastLoadingAssistant) return session

          return {
            ...session,
            messages: session.messages.map((message) =>
              message.id === lastLoadingAssistant.id
                ? {
                    ...message,
                    loading: false,
                    error: true,
                    text: `連線失敗，請確認後端服務是否啟動。\n\n錯誤訊息：${error.message}`,
                  }
                : message,
            ),
          }
        }),
      )
    },
  })

  const sortedSessions = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt)

  return {
    sessions: sortedSessions,
    currentSessionId,
    messages,
    isStreaming: mutation.isPending,
    sendMessage: (query: string) => mutation.mutate(query),
    startNewSession: () => setCurrentSessionId(null),
    switchSession: (id: string) => setCurrentSessionId(id),
    deleteSession: (id: string) => {
      setSessions((previous) => {
        const next = previous.filter((session) => session.id !== id)
        if (id === currentSessionId) setCurrentSessionId(next[0]?.id || null)
        return next
      })
    },
    clearChat: () => {
      if (!currentSessionId) return
      setSessions((previous) =>
        previous.map((session) =>
          session.id === currentSessionId ? { ...session, messages: [] } : session,
        ),
      )
    },
  }
}
