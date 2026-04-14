import { useState, useEffect, useRef } from 'react';
import { Sidebar } from './ui/Sidebar';
import { Avatar } from './ui/Avatar';
import { Waveform } from './ui/Waveform';
import { useVoicePipeline } from './hooks/useVoicePipeline';
import { useAgentStore } from './core/state/agentStore';
import { audioPlayer } from './core/audio/player';
import { DebugPanel } from './ui/DebugPanel';
import { TranscriptPanel } from './ui/TranscriptPanel';
import { useMetrics } from './hooks/useMetrics';
import { ParticleBackground } from './ui/ParticleBackground';
import { SettingsPanel } from './ui/SettingsPanel';

const STATE_LABELS: Record<string, string> = {
  idle: 'Ready',
  listening: '🎙️ Listening',
  thinking: '🧠 Processing',
  speaking: '🔊 Speaking',
  interrupting: '⏹️ Interrupted',
  error: '❌ Error',
};

const STATE_COLORS: Record<string, string> = {
  idle: 'bg-zinc-900/60 border-zinc-700/30 text-zinc-400',
  listening: 'bg-emerald-900/30 border-emerald-500/30 text-emerald-300 animate-pulse',
  thinking: 'bg-amber-900/30 border-amber-500/30 text-amber-300 animate-pulse',
  speaking: 'bg-indigo-900/30 border-indigo-500/30 text-indigo-300',
  interrupting: 'bg-red-900/30 border-red-500/30 text-red-300',
  error: 'bg-red-900/40 border-red-500/40 text-red-300',
};

function App() {
  const { startPipeline, stopPipeline } = useVoicePipeline();
  const { state, duplexEnabled, setDuplexEnabled, mode, volume, setVolume, chatHistory, response, partialTranscript } = useAgentStore();
  const [chatInput, setChatInput] = useState('');
  const prevVolumeRef = useRef(volume);

  // Determine if transcript reflects content worth showing
  const hasTranscript = chatHistory.length > 0 || response.length > 0 || partialTranscript.length > 0;
  const layoutPadding = hasTranscript ? 'md:pr-96' : '';

  // Start metrics polling
  useMetrics();

  // Sync volume with player
  useEffect(() => {
    audioPlayer.setVolume(volume);
  }, [volume]);

  const toggleMute = () => {
    if (volume > 0) {
      prevVolumeRef.current = volume;
      setVolume(0);
    } else {
      setVolume(prevVolumeRef.current || 50);
    }
  };

  const handleMicClick = () => {
    audioPlayer.resume();
    if (state === 'idle') {
      startPipeline();
    } else {
      stopPipeline();
    }
  };

  const handleChatSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!chatInput.trim()) return;
    const { addMessage } = useAgentStore.getState();
    addMessage({ role: 'user', content: chatInput.trim() });
    setChatInput('');
  };

  const toggleDuplex = () => {
    setDuplexEnabled(!duplexEnabled);
    if (!duplexEnabled && state === 'idle') {
      audioPlayer.resume();
      startPipeline();
    }
  };

  return (
    <div className="bg-surface text-on-surface min-h-screen overflow-hidden selection:bg-primary/30">
      {/* Ambient background particles */}
      <div className="fixed inset-0 pointer-events-none opacity-40 overflow-hidden">
        <div className="absolute top-[-10%] left-[-10%] w-[40%] h-[40%] bg-primary/10 blur-[120px] rounded-full animate-pulse"></div>
        <div className="absolute bottom-[-10%] right-[-10%] w-[40%] h-[40%] bg-indigo-500/10 blur-[120px] rounded-full animate-pulse" style={{ animationDelay: '2s' }}></div>
        
        <ParticleBackground />
      </div>

      <Sidebar />

      <main className={`relative h-screen w-full flex flex-col items-center p-6 bg-[var(--color-surface)] overflow-hidden transition-all duration-500 ${layoutPadding}`}>
        {/* Floating background gradient for the character area */}
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[var(--color-primary)]/5 blur-[150px] rounded-full pointer-events-none"></div>

        <Avatar />
        <DebugPanel />
        <Waveform />



        {/* 🚀 PRO DOCK: Three-Zone Professional Layout */}
        <div className={`fixed bottom-4 left-1/2 -translate-x-1/2 w-[95%] max-w-2xl px-2 z-50 transition-all duration-500 ${layoutPadding}`}>
          <div className="bg-zinc-950/60 backdrop-blur-2xl rounded-full px-4 py-3 flex items-center justify-between w-full shadow-[0_25px_60px_-15px_rgba(0,0,0,0.8)] border border-white/10 ring-1 ring-white/5">
            
            {/* ZONE 1: Audio Controls (Left) */}
            <div className="flex items-center gap-3 flex-1 min-w-0">
              <button 
                onClick={toggleMute}
                className={`flex items-center justify-center w-10 h-10 rounded-full transition-all ${
                  volume === 0 ? 'bg-red-500/10 text-red-400' : 'text-zinc-400 hover:text-white hover:bg-white/5'
                }`}
                title={volume === 0 ? 'Unmute' : 'Mute'}
              >
                <span className="material-symbols-outlined text-xl">
                  {volume === 0 ? 'volume_off' : volume < 50 ? 'volume_down' : 'volume_up'}
                </span>
              </button>
              
              <div className="hidden sm:flex flex-1 items-center max-w-[140px]">
                <input 
                  type="range" 
                  min="0" 
                  max="100" 
                  value={volume} 
                  onChange={(e) => setVolume(Number(e.target.value))}
                  className="w-full h-1.5 bg-white/10 rounded-full accent-[var(--color-primary)] cursor-pointer appearance-none transition-all hover:h-2"
                />
              </div>
              <span className="hidden sm:inline text-[10px] text-zinc-500 font-mono w-6 text-right">{volume}</span>
            </div>
            
            {/* ZONE 2: Primary Interaction (Center) */}
            <div className="flex items-center justify-center mx-4">
              <button 
                onClick={handleMicClick}
                className="relative group focus:outline-none"
                title={state === 'idle' ? 'Start Listening' : 'Stop'}
              >
                {/* Dynamic State Glows */}
                <div className={`absolute inset-0 rounded-full blur-2xl transition-all duration-500 opacity-0 group-hover:opacity-100 ${
                  state === 'listening' ? 'bg-emerald-500/20 opacity-100 scale-125' :
                  state === 'thinking' ? 'bg-amber-500/40 opacity-100 scale-150 animate-pulse' :
                  state === 'speaking' ? 'bg-[var(--color-primary)]/20 opacity-100 scale-125' :
                  'bg-[var(--color-primary)]/10'
                }`}></div>

                {/* Primary Button Core */}
                <div className={`w-14 h-14 rounded-full flex items-center justify-center relative z-10 shadow-2xl transition-all duration-300 transform group-active:scale-90 ${
                  state === 'error' ? 'bg-red-500' :
                  state === 'interrupting' ? 'bg-red-500 animate-[pulse_0.5s_infinite] shadow-lg shadow-red-500/50' :
                  state === 'listening' ? 'bg-emerald-500 shadow-emerald-500/30 animate-pulse' :
                  state === 'thinking' ? 'bg-amber-500 shadow-amber-500/30' :
                  state === 'speaking' ? 'bg-[var(--color-primary)] shadow-[var(--color-primary)]/30 scale-105' :
                  'bg-white/10 hover:bg-white/20 text-white'
                }`}>
                  <span className="material-symbols-outlined text-2xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                    {state === 'idle' ? 'mic' : state === 'listening' ? 'graphic_eq' : 'stop'}
                  </span>
                </div>

                {/* Duplex Pulse Indicator */}
                {duplexEnabled && (
                  <div className="absolute -inset-1 rounded-full border border-emerald-500/20 animate-[ping_3s_infinite] pointer-events-none"></div>
                )}
              </button>
            </div>

            {/* ZONE 3: System Controls (Right) */}
            <div className="flex items-center gap-2 flex-1 justify-end">
              {/* Stop AI Action */}
              <button 
                className="w-10 h-10 flex items-center justify-center text-zinc-400 hover:text-red-400 hover:bg-red-500/5 rounded-full transition-colors disabled:opacity-0" 
                onClick={stopPipeline} 
                title="Stop AI"
                disabled={state === 'idle'}
              >
                <span className="material-symbols-outlined text-2xl">stop_circle</span>
              </button>

              {/* Duplex Toggle */}
              <button 
                className={`w-10 h-10 flex items-center justify-center transition-all rounded-full ${
                  duplexEnabled ? 'text-emerald-400 bg-emerald-500/10' : 'text-zinc-500 hover:text-zinc-300 hover:bg-white/5'
                }`}
                onClick={toggleDuplex}
                title={duplexEnabled ? 'Duplex: ON' : 'Duplex: OFF'}
              >
                <div className="relative">
                  <span className="material-symbols-outlined text-2xl" style={duplexEnabled ? { fontVariationSettings: "'FILL' 1" } : {}}>bolt</span>
                  {duplexEnabled && (
                    <span className="absolute -top-1 -right-1 w-2 h-2 bg-emerald-500 rounded-full border border-zinc-950"></span>
                  )}
                </div>
              </button>
            </div>

          </div>
        </div>

        {/* Chat Input (Chat/Agent mode) */}
        {(mode === 'chat' || mode === 'agent') && (
          <form onSubmit={handleChatSubmit} className={`fixed bottom-24 left-1/2 -translate-x-1/2 w-full max-w-lg px-4 z-50 transition-all duration-500 ${layoutPadding}`}>
            <div className="flex items-center gap-3 bg-zinc-900/80 backdrop-blur-md rounded-full px-5 py-3 border border-white/10">
              <input
                type="text"
                value={chatInput}
                onChange={(e) => setChatInput(e.target.value)}
                placeholder="Type a message..."
                className="flex-1 bg-transparent text-sm text-white placeholder-zinc-500 outline-none font-inter"
              />
              <button type="submit" className="text-[var(--color-primary)] hover:text-white transition-colors">
                <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>send</span>
              </button>
            </div>
          </form>
        )}
      </main>

      {/* Transcript Panel (Right Side) */}
      <TranscriptPanel />

      {/* Settings Overlay */}
      <SettingsPanel />
    </div>
  );
}

export default App;
