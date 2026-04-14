import React from 'react';
import { useAgentStore } from '../core/state/agentStore';

export const DebugPanel: React.FC = () => {
  const { state, metrics, firstTokenLatencyMs, duplexEnabled, mode, showDebug } = useAgentStore();
  
  if (!showDebug) return null;

  const vadStatus = state === 'listening' ? 'Speech Detect' : state === 'idle' ? 'Standby' : 'Active';
  const asrStatus = state === 'listening' ? 'Streaming' : 'Ready';
  const llmStatus = state === 'thinking' ? 'Streaming' : state === 'speaking' ? 'Done' : 'Idle';
  const ttsStatus = state === 'speaking' ? 'Streaming' : 'Idle';

  const latencyDisplay = metrics?.latencyMs?.avg
    ? `${metrics.latencyMs.avg.toFixed(0)}ms`
    : firstTokenLatencyMs > 0
    ? `${firstTokenLatencyMs}ms`
    : '—';

  const tokensDisplay = metrics?.tokensPerSec
    ? `${metrics.tokensPerSec.toFixed(1)}/s`
    : '—';

  const errorDisplay = metrics ? `${metrics.errors}` : '0';

  return (
    <div className="absolute left-24 top-24 pointer-events-auto z-40 hidden lg:block">
      <div className="glass-panel p-4 rounded-xl border border-white/5 w-56 shadow-2xl bg-zinc-950/60 backdrop-blur-md">
        {/* Header */}
        <div className="flex items-center gap-2 mb-3 border-b border-white/10 pb-2">
          <span className="material-symbols-outlined text-[var(--color-primary)] text-sm">monitor_heart</span>
          <p className="text-[10px] text-zinc-300 uppercase tracking-widest font-manrope font-semibold">Infra Metrics</p>
        </div>
        
        {/* Metrics Grid */}
        <div className="flex flex-col gap-2 font-mono text-[10px]">
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Mode</span>
            <span className="text-[var(--color-primary)] capitalize">{mode}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Duplex</span>
            <span className={duplexEnabled ? 'text-emerald-400' : 'text-zinc-500'}>{duplexEnabled ? 'ON' : 'OFF'}</span>
          </div>
          
          <div className="w-full h-px bg-white/5 my-1"></div>
          
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Latency</span>
            <span className="text-emerald-400">{latencyDisplay}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Tokens</span>
            <span className="text-emerald-400">{tokensDisplay}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Errors</span>
            <span className={metrics && metrics.errors > 0 ? 'text-red-400' : 'text-zinc-300'}>{errorDisplay}</span>
          </div>

          <div className="w-full h-px bg-white/5 my-1"></div>

          <div className="flex justify-between items-center">
            <span className="text-zinc-500">VAD</span>
            <span className={state === 'listening' ? 'text-emerald-400' : 'text-zinc-400'}>{vadStatus}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">Echo Cancel</span>
            <span className="text-emerald-400">ON</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">ASR</span>
            <span className={state === 'listening' ? 'text-emerald-400' : 'text-zinc-400'}>{asrStatus}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">LLM</span>
            <span className={state === 'thinking' ? 'text-amber-400' : state === 'speaking' ? 'text-[var(--color-primary)]' : 'text-zinc-400'}>{llmStatus}</span>
          </div>
          <div className="flex justify-between items-center">
            <span className="text-zinc-500">TTS</span>
            <span className={state === 'speaking' ? 'text-cyan-400' : 'text-zinc-400'}>{ttsStatus}</span>
          </div>
        </div>

        {/* First Token Latency */}
        {firstTokenLatencyMs > 0 && (
          <div className="mt-3 pt-2 border-t border-white/5">
            <div className="flex justify-between items-center font-mono text-[10px]">
              <span className="text-zinc-500">1st Token</span>
              <span className={firstTokenLatencyMs < 500 ? 'text-emerald-400' : firstTokenLatencyMs < 1000 ? 'text-amber-400' : 'text-red-400'}>
                {firstTokenLatencyMs}ms
              </span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
