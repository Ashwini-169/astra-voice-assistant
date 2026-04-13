import React from 'react';

export const Sidebar: React.FC = () => {
  return (
    <aside className="fixed left-0 top-0 h-full py-12 z-40 hidden md:flex flex-col items-center bg-zinc-950/40 backdrop-blur-xl w-20 rounded-r-3xl shadow-[0_0_40px_rgba(99,102,241,0.08)]">
      <div className="flex flex-col items-center gap-1 mb-12">
        <div className="w-10 h-10 rounded-xl bg-[var(--color-primary)]/20 flex items-center justify-center mb-2">
          <span className="material-symbols-outlined text-[var(--color-primary)]">bubble_chart</span>
        </div>
      </div>
      <nav className="flex flex-col gap-8 flex-1">
        <button className="text-indigo-400 scale-110 flex flex-col items-center gap-1 group">
          <span className="material-symbols-outlined">bubble_chart</span>
          <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">Presence</span>
        </button>
        <button className="text-zinc-600 hover:text-zinc-300 transition-all flex flex-col items-center gap-1 group">
          <span className="material-symbols-outlined">database</span>
          <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">Memory</span>
        </button>
        <button className="text-zinc-600 hover:text-zinc-300 transition-all flex flex-col items-center gap-1 group">
          <span className="material-symbols-outlined">psychology</span>
          <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">Synthesis</span>
        </button>
        <button className="text-zinc-600 hover:text-zinc-300 transition-all flex flex-col items-center gap-1 group">
          <span className="material-symbols-outlined">query_stats</span>
          <span className="font-manrope text-[8px] uppercase tracking-widest opacity-0 group-hover:opacity-100 transition-opacity">Vitals</span>
        </button>
      </nav>
    </aside>
  );
};
