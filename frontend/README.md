# Study Abroad Consultant — Frontend

> React interface for streaming LangGraph agent progress and final answers in real time.

---

## Design Philosophy

The frontend is built to be **minimalist, focused, and responsive**. It provides a document-centric layout where the conversation takes center stage, mimicking the flow of modern AI productivity tools.

### Key Features
- **Chat-Centric UX**: Focused conversation layout with local session history and quick follow-up interactions.
- **Real-Time Agent Feedback**: Visualizes `thinking`, `tool_call`, and `tool_result` events emitted by the LangGraph backend workflow.
- **SSE Streaming**: High-performance Server-Sent Events integration for low-latency response streaming.
- **Persistent Sessions**: Chat history is stored locally in the browser, allowing users to return to previous consultations.
- **Tailwind v4 Aesthetics**: Leverages the latest CSS capabilities for smooth transitions and a premium look.

---

## Tech Stack

- **Framework**: [React 19](https://react.dev/) + [TypeScript](https://www.typescriptlang.org/)
- **Build Tool**: [Vite](https://vitejs.dev/)
- **Styling**: [Tailwind CSS v4](https://tailwindcss.com/)
- **Icons**: [Lucide React](https://lucide.dev/)
- **Markdown**: `react-markdown` with GFM support
- **State & Streaming**: TanStack Query + custom streamed chat hook

---

## Component Architecture

```text
src/
├── components/
│   ├── AgentSteps.tsx      # Renders streamed agent events
│   ├── ChatInput.tsx       # Text input and submit handling
│   ├── MessageBubble.tsx   # Markdown renderer for chat messages
│   ├── ResumeUpload.tsx    # Resume upload UI
│   ├── SettingsModal.tsx   # Runtime settings modal
│   └── UserProfileModal.tsx# User profile form modal
├── hooks/
│   └── useStreamChat.ts    # Session state and streamed chat event handling
└── types.ts                # Shared TypeScript interfaces
```

The frontend expects the backend `POST /api/chat` endpoint to return streamed lines in the form `data: {json}` where each JSON payload matches one of the shared agent event types.

---

## Installation & Usage

1. **Install Dependencies**
   ```bash
   cd frontend
   npm install
   ```

2. **Run Dev Server**
   ```bash
   npm run dev
   ```

3. **Access the App**
   Open `http://localhost:5173`. Make sure the backend is running on port `8000`.

---
