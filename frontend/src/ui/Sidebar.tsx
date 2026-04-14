import React from 'react';
import { useAgentStore, type AppMode } from '../core/state/agentStore';

const MODES: { key: AppMode; icon: string; label: string }[] = [
  { key: 'voice', icon: 'mic', label: 'Voice' },
  { key: 'chat', icon: 'forum', label: 'Chat' },
  { key: 'agent', icon: 'psychology', label: 'Agent' },
];

const NAV_ITEMS = [
  { icon: 'memory', label: 'Memory' },
  { icon: 'settings', label: 'Settings' },
];

export const Sidebar: React.FC = () => {
  const { mode, setMode, showDebug, setShowDebug } = useAgentStore();

  return (
    <aside className="fixed left-0 top-0 h-full py-12 z-40 hidden md:flex flex-col items-center bg-zinc-950/40 backdrop-blur-xl w-20 rounded-r-3xl shadow-[0_0_40px_rgba(99,102,241,0.08)]">
      {/* Logo */}
      <div className="flex flex-col items-center gap-1 mb-8">
        <div className="w-10 h-10 rounded-xl bg-[var(--color-primary)]/20 flex items-center justify-center mb-2">
          <span className="material-symbols-outlined text-[var(--color-primary)]">bubble_chart</span>
        </div>
      </div>

      {/* Mode Switcher */}
      <div className="flex flex-col gap-2 mb-8 p-1.5 bg-zinc-900/50 rounded-2xl border border-white/5">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => setMode(m.key)}
            className={`flex flex-col items-center gap-1 p-2 rounded-xl transition-all duration-200 group ${
              mode === m.key
                ? 'bg-[var(--color-primary)]/20 text-[var(--color-primary)] scale-105'
                : 'text-zinc-600 hover:text-zinc-300 hover:bg-zinc-800/50'
            }`}
            title={m.label}
          >
            <span className="material-symbols-outlined text-lg" style={mode === m.key ? { fontVariationSettings: "'FILL' 1" } : {}}>
              {m.icon}
            </span>
            <span className="font-manrope text-[7px] uppercase tracking-widest">{m.label}</span>
          </button>
        ))}
      </div>

      {/* Navigation */}
      <nav className="flex flex-col gap-6 flex-1">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.label}
            className="text-zinc-600 hover:text-zinc-300 transition-all flex flex-col items-center gap-1 group"
          >
            <span className="material-symbols-outlined">{item.icon}</span>
            <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">{item.label}</span>
          </button>
        ))}
      </nav>

      {/* Debug (bottom) */}
      <button 
        onClick={() => setShowDebug(!showDebug)}
        className={`transition-all flex flex-col items-center gap-1 group mb-4 ${
          showDebug ? 'text-[var(--color-primary)] scale-110' : 'text-zinc-600 hover:text-zinc-300'
        }`}
      >
        <span className="material-symbols-outlined" style={showDebug ? { fontVariationSettings: "'FILL' 1" } : {}}>speed</span>
        <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">Debug</span>
      </button>
    </aside>
  );
};
