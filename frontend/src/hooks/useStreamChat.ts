import { useState, useEffect } from 'react'
import { useMutation } from '@tanstack/react-query'
import type { AgentEvent, Message, ChatSession } from '../types'

const STORAGE_KEY = 'study_abroad_chat_sessions'

export function useStreamChat() {
  const [sessions, setSessions] = useState<ChatSession[]>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY)
      if (!saved) return []
      const parsed: ChatSession[] = JSON.parse(saved)
      // Ensure events exists on all messages
      return parsed.map(s => ({
        ...s,
        messages: s.messages.map(m => ({
          ...m,
          events: m.events || []
        }))
      }))
    } catch {
      return []
    }
  })

  const [currentSessionId, setCurrentSessionId] = useState<string | null>(sessions[0]?.id || null)

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions))
  }, [sessions])

  const currentSession = sessions.find(s => s.id === currentSessionId)
  const messages = currentSession?.messages || []

  const getOrCreateSession = (firstMessageText: string) => {
    if (currentSessionId && sessions.some(s => s.id === currentSessionId)) {
      return currentSessionId
    }
    const newSessionId = crypto.randomUUID()

    const maxLen = 20
    let title = firstMessageText.slice(0, maxLen)
    if (firstMessageText.length > maxLen) title += '...'

    const newSession: ChatSession = {
      id: newSessionId,
      title,
      updatedAt: Date.now(),
      messages: []
    }
    setSessions(prev => [newSession, ...prev])
    setCurrentSessionId(newSessionId)
    return newSessionId
  }

  const mutation = useMutation({
    mutationFn: async (query: string) => {
      const targetSessionId = getOrCreateSession(query)
      const assistantId = crypto.randomUUID()

      const userMessage: Message = { id: crypto.randomUUID(), role: 'user', text: query, events: [], loading: false }
      const assistantMessage: Message = { id: assistantId, role: 'assistant', text: '', events: [], loading: true }

      setSessions(prev =>
        prev.map(s => {
          if (s.id !== targetSessionId) return s
          return { ...s, messages: [...s.messages, userMessage, assistantMessage], updatedAt: Date.now() }
        })
      )

      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
      })

      if (!res.ok) throw new Error(`HTTP ${res.status}`)

      const reader = res.body!.getReader()
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
            setSessions(prev =>
              prev.map(s => {
                if (s.id !== targetSessionId) return s
                return {
                  ...s,
                  messages: s.messages.map(m => {
                    if (m.id !== assistantId) return m
                    if (event.type === 'answer_chunk')
                      return { ...m, text: m.text + event.text }
                    const base = { ...m, events: [...m.events, event] }
                    if (event.type === 'answer')
                      return { ...base, text: event.text, loading: false }
                    if (event.type === 'error')
                      return { ...base, text: event.message, loading: false, error: true }
                    return base
                  })
                }
              })
            )
          } catch {
            // ignore malformed SSE line
          }
        }
      }
    },

    onError: (error) => {
      setSessions(prev => prev.map(s => {
        if (s.id !== currentSessionId) return s
        const last = [...s.messages].reverse().find(m => m.role === 'assistant' && m.loading)
        if (!last) return s
        return {
          ...s,
          messages: s.messages.map(m =>
            m.id === last.id
              ? { ...m, loading: false, error: true, text: `連線錯誤，請稍後再試。\n\n錯誤詳情：${error.message}` }
              : m
          )
        }
      }))
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
      setSessions(prev => {
        const next = prev.filter(s => s.id !== id)
        if (id === currentSessionId) setCurrentSessionId(next[0]?.id || null)
        return next
      })
    },
    clearChat: () => {
      if (currentSessionId) {
         setSessions(prev => prev.map(s => s.id === currentSessionId ? { ...s, messages: [] } : s))
      }
    },
  }
}
