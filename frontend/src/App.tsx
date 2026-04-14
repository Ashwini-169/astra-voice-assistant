import { useState, useEffect } from 'react';
import { Sidebar } from './ui/Sidebar';
import { Avatar } from './ui/Avatar';
import { Waveform } from './ui/Waveform';
import { useVoicePipeline } from './hooks/useVoicePipeline';
import { useAgentStore } from './core/state/agentStore';
import { audioPlayer } from './core/audio/player';
import { DebugPanel } from './ui/DebugPanel';
import { TranscriptPanel } from './ui/TranscriptPanel';
import { useMetrics } from './hooks/useMetrics';

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

  // ... rest of handlers ...

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
      {/* ... header ... */}
      <Sidebar />

      <main className={`relative h-screen w-full flex flex-col items-center p-6 bg-[var(--color-surface)] overflow-hidden transition-all duration-500 ${layoutPadding}`}>
        {/* ... environment background ... */}
        <Avatar />
        <DebugPanel />
        <Waveform />

        {/* Enhanced Bottom Controls: Larger Pill with Inline Volume Bar */}
        <div className={`fixed bottom-0 left-1/2 -translate-x-1/2 w-full px-4 pb-10 flex flex-col items-center z-50 transition-all duration-500 md:max-w-2xl ${layoutPadding}`}>
          <div className="bg-zinc-950/50 backdrop-blur-2xl rounded-full px-8 py-5 flex items-center justify-between w-full shadow-[0_25px_60px_-15px_rgba(0,0,0,0.7)] border border-white/10">
            
            {/* Inline Volume Controls */}
            <div className="flex items-center gap-4 flex-1 max-w-[240px]">
              <button 
                onClick={toggleMute}
                className={`flex items-center justify-center w-10 h-10 transition-colors ${volume === 0 ? 'text-red-400' : 'text-zinc-500 hover:text-white'}`}
                title={volume === 0 ? 'Unmute' : 'Mute'}
              >
                <span className="material-symbols-outlined text-2xl">
                  {volume === 0 ? 'volume_off' : volume < 50 ? 'volume_down' : 'volume_up'}
                </span>
              </button>
              
              <div className="relative flex-1 group">
                <input 
                  type="range" 
                  min="0" 
                  max="100" 
                  value={volume} 
                  onChange={(e) => setVolume(Number(e.target.value))}
                  className="w-full h-2 bg-white/10 rounded-full accent-[var(--color-primary)] outline-none cursor-pointer appearance-none transition-all hover:h-2.5"
                />
                <div 
                  className="absolute left-0 top-0 h-full bg-[var(--color-primary)]/20 pointer-events-none rounded-full" 
                  style={{ width: `${volume}%` }}
                ></div>
              </div>
              <span className="text-[10px] text-zinc-500 font-mono w-6 text-right">{volume}</span>
            </div>
            
            <div className="flex items-center gap-6 ml-6">
              {/* Mic Toggle: Larger & More Tactile */}
              <button 
                className="group relative" 
                onClick={handleMicClick}
                title={state === 'idle' ? 'Start Listening' : 'Stop'}
              >
                <div className={`absolute -inset-4 bg-[var(--color-primary)]/15 rounded-full blur-2xl transition-all ${state !== 'idle' ? 'bg-[var(--color-primary)]/40 scale-125' : 'group-hover:bg-[var(--color-primary)]/25'}`}></div>
                <div className={`w-16 h-16 rounded-full flex items-center justify-center relative z-10 shadow-2xl group-active:scale-90 transition-transform ${
                  state === 'error' 
                    ? 'bg-red-500 shadow-lg shadow-red-500/30'
                    : state !== 'idle'
                    ? 'bg-red-500 shadow-lg shadow-red-500/30 animate-pulse'
                    : 'bg-gradient-to-br from-[var(--color-secondary)] to-[var(--color-primary)] shadow-lg shadow-[var(--color-primary)]/30'
                }`}>
                  <span className="material-symbols-outlined text-white text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                    {state !== 'idle' ? 'stop' : 'mic'}
                  </span>
                </div>
              </button>

              {/* Stop AI */}
              <button 
                className="flex items-center justify-center w-12 h-12 text-zinc-500 hover:text-red-400 transition-colors disabled:opacity-10" 
                onClick={stopPipeline} 
                title="Stop AI"
                disabled={state === 'idle'}
              >
                <span className="material-symbols-outlined text-3xl">stop_circle</span>
              </button>

              {/* Duplex Toggle */}
              <button 
                className={`flex items-center justify-center w-12 h-12 transition-all rounded-full ${
                  duplexEnabled ? 'text-emerald-400 bg-emerald-400/10' : 'text-zinc-500 hover:text-zinc-300'
                }`}
                onClick={toggleDuplex}
                title={duplexEnabled ? 'Duplex: ON' : 'Duplex: OFF'}
              >
                <span className="material-symbols-outlined text-3xl" style={duplexEnabled ? { fontVariationSettings: "'FILL' 1" } : {}}>bolt</span>
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

      {/* BottomNavBar (Mobile Only) */}
      <nav className="md:hidden fixed bottom-0 left-1/2 -translate-x-1/2 w-full px-4 pb-8 flex justify-around items-center z-50">
        <div className="flex items-center justify-around w-full max-w-md mx-auto mb-6 bg-zinc-950/20 backdrop-blur-md rounded-t-[2.5rem] py-4">
          <div className="flex flex-col items-center justify-center text-zinc-500">
            <span className="material-symbols-outlined">mic</span>
            <span className="font-manrope font-medium text-[10px]">Listen</span>
          </div>
          <div className="flex flex-col items-center justify-center bg-indigo-500/20 text-indigo-200 rounded-full w-14 h-14">
            <span className="material-symbols-outlined" style={{ fontVariationSettings: "'FILL' 1" }}>flare</span>
            <span className="font-manrope font-medium text-[10px]">Think</span>
          </div>
          <div className="flex flex-col items-center justify-center text-zinc-500">
            <span className="material-symbols-outlined">terminal</span>
            <span className="font-manrope font-medium text-[10px]">Command</span>
          </div>
        </div>
      </nav>

    </div>
  );
}

export default App;
