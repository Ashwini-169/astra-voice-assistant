import { create } from 'zustand';

interface SettingsStore {
  provider: string;
  model: string;
  bargeInRmsNormal: number;
  bargeInRmsEcho: number;
  isSettingsOpen: boolean;
  setProvider: (provider: string) => void;
  setModel: (model: string) => void;
  setBargeInRmsNormal: (val: number) => void;
  setBargeInRmsEcho: (val: number) => void;
  setSettingsOpen: (isOpen: boolean) => void;
}

export const useSettingsStore = create<SettingsStore>((set) => ({
  provider: 'ollama',
  model: 'qwen2.5:3b',
  bargeInRmsNormal: 900,
  bargeInRmsEcho: 2000,
  isSettingsOpen: false,
  setProvider: (provider) => set({ provider }),
  setModel: (model) => set({ model }),
  setBargeInRmsNormal: (bargeInRmsNormal) => set({ bargeInRmsNormal }),
  setBargeInRmsEcho: (bargeInRmsEcho) => set({ bargeInRmsEcho }),
  setSettingsOpen: (isSettingsOpen) => set({ isSettingsOpen }),
}));
