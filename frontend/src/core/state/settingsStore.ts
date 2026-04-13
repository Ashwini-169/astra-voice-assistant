import { create } from 'zustand';

interface SettingsStore {
  provider: string;
  model: string;
  setProvider: (provider: string) => void;
  setModel: (model: string) => void;
}

export const useSettingsStore = create<SettingsStore>((set) => ({
  provider: 'ollama',
  model: 'qwen2.5:3b',
  setProvider: (provider) => set({ provider }),
  setModel: (model) => set({ model }),
}));
