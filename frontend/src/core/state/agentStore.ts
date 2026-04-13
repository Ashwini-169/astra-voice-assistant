import { create } from 'zustand';

export type AgentState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'interrupting';

interface AgentStore {
  state: AgentState;
  transcript: string;
  response: string;
  setState: (state: AgentState) => void;
  setTranscript: (transcript: string) => void;
  setResponse: (response: string) => void;
  appendResponse: (chunk: string) => void;
  reset: () => void;
}

export const useAgentStore = create<AgentStore>((set) => ({
  state: 'idle',
  transcript: '',
  response: '',
  setState: (state) => set({ state }),
  setTranscript: (transcript) => set({ transcript }),
  setResponse: (response) => set({ response }),
  appendResponse: (chunk) => set((s) => ({ response: s.response + chunk })),
  reset: () => set({ state: 'idle', transcript: '', response: '' }),
}));
