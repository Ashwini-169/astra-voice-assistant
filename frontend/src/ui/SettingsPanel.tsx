import React, { useEffect, useState } from 'react';
import { useSettingsStore } from '../core/state/settingsStore';

export const SettingsPanel = () => {
  const {
    isSettingsOpen, setSettingsOpen,
    model, setModel, provider, setProvider,
    bargeInRmsNormal, setBargeInRmsNormal,
    bargeInRmsEcho, setBargeInRmsEcho
  } = useSettingsStore();

  const [models, setModels] = useState<string[]>([]);
  const [loadingModels, setLoadingModels] = useState(false);
  const [errorModels, setErrorModels] = useState('');

  useEffect(() => {
    if (!isSettingsOpen) return;

    const fetchOllamaModels = async () => {
      setLoadingModels(true);
      setErrorModels('');
      try {
        const response = await fetch('http://127.0.0.1:11434/api/tags');
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        const data = await response.json();
        
        if (data && data.models) {
          const modelNames = data.models.map((m: any) => m.name);
          setModels(modelNames);
          
          // Auto-select if current model is not in the list, but list isn't empty
          if (modelNames.length > 0 && !modelNames.includes(model)) {
            setModel(modelNames[0]);
          }
        }
      } catch (e: any) {
        console.error('Failed to fetch Ollama models:', e);
        setErrorModels('Could not connect to Ollama. Ensure it is running and OLLAMA_ORIGINS="*" is set.');
      } finally {
        setLoadingModels(false);
      }
    };

    if (provider === 'ollama') {
      fetchOllamaModels();
    }
  }, [isSettingsOpen, provider]);

  if (!isSettingsOpen) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      {/* Backdrop */}
      <div 
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={() => setSettingsOpen(false)}
      />

      {/* Modal */}
      <div className="relative w-full max-w-md bg-zinc-900/90 border border-white/5 backdrop-blur-xl rounded-3xl p-6 shadow-2xl font-manrope text-zinc-300">
        <div className="flex justify-between items-center mb-6">
          <h2 className="text-xl font-bold bg-gradient-to-r from-blue-300 to-indigo-300 bg-clip-text text-transparent">Voice Settings</h2>
          <button 
            onClick={() => setSettingsOpen(false)}
            className="w-8 h-8 flex items-center justify-center rounded-full bg-white/5 hover:bg-white/10 transition-colors"
          >
            <span className="material-symbols-outlined text-sm">close</span>
          </button>
        </div>

        <div className="space-y-6">
          {/* Section: AI Provider & Model */}
          <div className="space-y-3 p-4 bg-white/[0.02] border border-white/5 rounded-2xl">
            <h3 className="text-xs uppercase tracking-widest text-zinc-500 font-semibold mb-2 flex items-center gap-2">
              <span className="material-symbols-outlined text-[14px]">psychology</span>
              Language Model
            </h3>
            
            <div className="flex flex-col gap-1">
              <label className="text-[10px] text-zinc-400 pl-1">Provider</label>
              <select 
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="w-full bg-zinc-800/50 border border-white/10 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors"
              >
                <option value="ollama">Ollama (Local)</option>
                {/* Fallbacks if other providers are supported in the future */}
                <option value="openai">OpenAI (Unavailable)</option>
              </select>
            </div>

            {provider === 'ollama' && (
              <div className="flex flex-col gap-1">
                <label className="text-[10px] text-zinc-400 pl-1 flex justify-between">
                  <span>Model</span>
                  {loadingModels && <span className="animate-pulse text-indigo-400">Loading...</span>}
                </label>
                <select 
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  disabled={loadingModels || models.length === 0}
                  className="w-full bg-zinc-800/50 border border-white/10 rounded-xl px-3 py-2 text-sm focus:outline-none focus:border-indigo-500/50 transition-colors disabled:opacity-50"
                >
                  {models.length > 0 ? (
                    models.map(m => (
                      <option key={m} value={m}>{m}</option>
                    ))
                  ) : (
                    <option value={model}>{model} (Selected)</option>
                  )}
                </select>
                {errorModels && (
                  <p className="text-[10px] text-red-400/80 mt-1 leading-tight">{errorModels}</p>
                )}
              </div>
            )}
          </div>

          {/* Section: Audio Tuning */}
          <div className="space-y-4 p-4 bg-white/[0.02] border border-white/5 rounded-2xl">
            <h3 className="text-xs uppercase tracking-widest text-zinc-500 font-semibold mb-4 flex items-center gap-2">
              <span className="material-symbols-outlined text-[14px]">graphic_eq</span>
              Microphone Sensitivity
            </h3>
            
            <div className="flex flex-col gap-2">
              <div className="flex justify-between items-baseline px-1">
                <label className="text-xs text-zinc-300">Silent Environment (Normal)</label>
                <span className="text-[10px] font-mono text-indigo-300">{bargeInRmsNormal}</span>
              </div>
              <input 
                type="range" 
                min="100" max="3000" step="50"
                value={bargeInRmsNormal}
                onChange={(e) => setBargeInRmsNormal(parseInt(e.target.value, 10))}
                className="w-full h-1 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-indigo-400"
              />
              <div className="flex justify-between text-[9px] text-zinc-500 px-1">
                <span>More Sensitive</span>
                <span className="text-zinc-400">Rec: 900</span>
                <span>Less Sensitive</span>
              </div>
            </div>

            <div className="flex flex-col gap-2 pt-2 border-t border-white/5">
              <div className="flex justify-between items-baseline px-1">
                <label className="text-xs text-zinc-300">While AI is Speaking (Echo Rejection)</label>
                <span className="text-[10px] font-mono text-purple-300">{bargeInRmsEcho}</span>
              </div>
              <input 
                type="range" 
                min="500" max="5000" step="100"
                value={bargeInRmsEcho}
                onChange={(e) => setBargeInRmsEcho(parseInt(e.target.value, 10))}
                className="w-full h-1 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-purple-400"
              />
              <div className="flex justify-between text-[9px] text-zinc-500 px-1">
                <span>More Sensitive</span>
                <span className="text-zinc-400">Rec: 2000</span>
                <span>Less Sensitive</span>
              </div>
              <p className="text-[10px] text-zinc-500 bg-white/5 p-2 rounded-lg mt-1 italic leading-relaxed">
                Prevents the AI from hearing its own voice through your speakers. If the AI interrupts itself, increase this value.
              </p>
            </div>
          </div>

        </div>
      </div>
    </div>
  );
};
