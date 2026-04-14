import { create } from 'zustand';

export type AgentState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'interrupting' | 'error';
export type AppMode = 'voice' | 'chat' | 'agent';

export interface ChatMessage {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp: number;
  toolName?: string;
}

export interface MetricsSnapshot {
  latencyMs: { avg: number; p95: number; samples: number };
  tokensPerSec: number;
  requests: number;
  errors: number;
  errorRate: number;
}

interface AgentStore {
  // Core State
  state: AgentState;
  mode: AppMode;
  duplexEnabled: boolean;

  // Transcripts
  transcript: string;          // Final ASR transcript
  partialTranscript: string;   // Interim ASR (for early LLM start)
  response: string;            // Current LLM response (streaming)

  // Chat History
  chatHistory: ChatMessage[];

  micRms: number;              // 0–1 normalized RMS from VAD for orb animation
  playbackRms: number;         // 0–1 normalized RMS from TTS playback for mouth animation
  isAudioPlaying: boolean;     // Current speaker playback state
  showDebug: boolean;          // Visibility of debug metrics panel

  // Metrics
  metrics: MetricsSnapshot | null;
  firstTokenLatencyMs: number; // Time from query send to first token

  // Audio Controls
  volume: number;              // 0–100

  // Core actions
  setState: (state: AgentState) => void;
  setMode: (mode: AppMode) => void;
  setDuplexEnabled: (enabled: boolean) => void;
  setTranscript: (transcript: string) => void;
  setPartialTranscript: (partial: string) => void;
  setResponse: (response: string) => void;
  appendResponse: (chunk: string) => void;
  setMicRms: (rms: number) => void;
  setPlaybackRms: (rms: number) => void;
  setIsAudioPlaying: (playing: boolean) => void;
  setShowDebug: (show: boolean) => void;
  setMetrics: (metrics: MetricsSnapshot) => void;
  setFirstTokenLatency: (ms: number) => void;
  setVolume: (volume: number) => void;
  addMessage: (msg: Omit<ChatMessage, 'id' | 'timestamp'>) => void;
  reset: () => void;
  softReset: () => void; // Reset transcript/response but keep history
}

let _msgId = 0;
const nextId = () => `msg_${++_msgId}_${Date.now()}`;

export const useAgentStore = create<AgentStore>((set) => ({
  state: 'idle',
  mode: 'voice',
  duplexEnabled: true,
  transcript: '',
  partialTranscript: '',
  response: '',
  chatHistory: [],
  micRms: 0,
  playbackRms: 0,
  isAudioPlaying: false,
  showDebug: false,
  metrics: null,
  firstTokenLatencyMs: 0,
  volume: 100,

  setState: (state) => set({ state }),
  setMode: (mode) => set({ mode }),
  setDuplexEnabled: (enabled) => set({ duplexEnabled: enabled }),
  setTranscript: (transcript) => set({ transcript }),
  setPartialTranscript: (partial) => set({ partialTranscript: partial }),
  setResponse: (response) => set({ response }),
  appendResponse: (chunk) => set((s) => ({ response: s.response + chunk })),
  setMicRms: (rms) => set({ micRms: rms }),
  setPlaybackRms: (rms) => set({ playbackRms: rms }),
  setIsAudioPlaying: (playing) => set({ isAudioPlaying: playing }),
  setShowDebug: (show) => set({ showDebug: show }),
  setMetrics: (metrics) => set({ metrics }),
  setFirstTokenLatency: (ms) => set({ firstTokenLatencyMs: ms }),
  setVolume: (volume) => set({ volume }),

  addMessage: (msg) => set((s) => ({
    chatHistory: [...s.chatHistory, { ...msg, id: nextId(), timestamp: Date.now() }],
  })),

  reset: () => set({
    state: 'idle',
    transcript: '',
    partialTranscript: '',
    response: '',
    micRms: 0,
    playbackRms: 0,
    isAudioPlaying: false,
    showDebug: false,
    firstTokenLatencyMs: 0,
  }),

  softReset: () => set({
    transcript: '',
    partialTranscript: '',
    response: '',
    micRms: 0,
    playbackRms: 0,
    isAudioPlaying: false,
    showDebug: false,
    firstTokenLatencyMs: 0,
  }),
}));
