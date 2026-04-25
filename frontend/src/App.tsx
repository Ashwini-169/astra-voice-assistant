import { useState, useEffect, useRef } from 'react';
import { Sidebar } from './ui/Sidebar';
import { Avatar } from './ui/Avatar';
import { Waveform } from './ui/Waveform';
import { useVoicePipeline } from './hooks/useVoicePipeline';
import { useAgentStore } from './core/state/agentStore';
import type { AppMode } from './core/state/agentStore';
import { audioPlayer } from './core/audio/player';
import { DebugPanel } from './ui/DebugPanel';
import { TranscriptPanel } from './ui/TranscriptPanel';
import { useMetrics } from './hooks/useMetrics';
import { ParticleBackground } from './ui/ParticleBackground';
import { SettingsPanel } from './ui/SettingsPanel';
import { MCPPanel } from './ui/MCPPanel';

function App() {
  const { startPipeline, stopPipeline, handleStream } = useVoicePipeline();
  const {
    state, duplexEnabled, setDuplexEnabled, mode, volume, setVolume,
    chatHistory, response, partialTranscript, setMode, setTranscript,
    setPartialTranscript, setResponse, setMicRms, setPlaybackRms,
    setIsAudioPlaying,
  } = useAgentStore();
  const [chatInput, setChatInput] = useState('');
  const prevVolumeRef = useRef(volume);

  // Determine if transcript reflects content worth showing
  const hasTranscript = chatHistory.length > 0 || response.length > 0 || partialTranscript.length > 0;
  const layoutPadding = mode === 'voice' && hasTranscript ? 'md:pr-96' : '';

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
    const text = chatInput.trim();
    if (text.length < 3) return;

    const { addMessage } = useAgentStore.getState();
    const modeSnapshot = mode;
    console.log('[Chat Submit]', { mode: modeSnapshot, source: 'typed', isFinal: true, text });
    addMessage({ role: 'user', content: text });
    setChatInput('');
    handleStream(text, modeSnapshot, 'typed', true);
  };

  const switchMode = async (newMode: AppMode) => {
    if (newMode === mode) return;

    await stopPipeline();
    audioPlayer.stop();
    setTranscript('');
    setPartialTranscript('');
    setResponse('');
    setMicRms(0);
    setPlaybackRms(0);
    setIsAudioPlaying(false);
    setDuplexEnabled(newMode === 'voice');
    setMode(newMode);
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

      <Sidebar onModeChange={switchMode} />

      <main className={`relative h-screen w-full flex flex-col p-6 bg-[var(--color-surface)] overflow-hidden transition-all duration-500 pl-4 md:pl-24`}>
        {/* Floating background gradient for the character area */}
        {mode === 'voice' && (
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[var(--color-primary)]/5 blur-[150px] rounded-full pointer-events-none"></div>
        )}

        {mode === 'voice' ? (
          <div className={`w-full flex-1 flex flex-col items-center justify-center transition-all ${layoutPadding}`}>
            <Avatar />
            <DebugPanel />
            <Waveform />
          </div>
        ) : (
          <div className="flex-1 w-full flex flex-col relative h-full items-center">
            {/* Small Avatar on Top Right - Keeps motion running! */}
            <div className="absolute top-0 right-0 transform scale-[0.4] origin-top-right z-10 pointer-events-none mix-blend-screen opacity-90">
              <Avatar />
            </div>
            {/* Embedded ChatGPT style view */}
            <TranscriptPanel inline={true} />
            <DebugPanel />
          </div>
        )}



        {/* 🚀 PRO DOCK: Dynamic Layout dependent on AppMode */}
        <div className={
          mode === 'voice' 
            ? `fixed bottom-4 left-1/2 -translate-x-1/2 w-[95%] max-w-2xl px-2 z-50 transition-all duration-500 ${layoutPadding}`
            : `fixed top-48 right-10 w-16 z-50 transition-all duration-500 origin-top`
        }>
          <div className={`bg-zinc-950/60 backdrop-blur-2xl border border-white/10 ring-1 ring-white/5 flex shadow-[0_25px_60px_-15px_rgba(0,0,0,0.8)] ${
            mode === 'voice' 
              ? "rounded-full px-4 py-3 flex-row items-center justify-between w-full"
              : "rounded-[2rem] py-6 px-2 flex-col items-center gap-6"
          }`}>
            
            {/* ZONE 1: Audio Controls */}
            <div className={`flex items-center gap-3 flex-1 min-w-0 ${mode !== 'voice' ? 'flex-col' : ''}`}>
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
              
              <div className={`hidden sm:flex flex-1 items-center max-w-[140px] ${mode !== 'voice' ? 'hidden sm:hidden hidden-absolute' : ''}`}>
                {mode === 'voice' && (
                  <input 
                    type="range" 
                    min="0" 
                    max="100" 
                    value={volume} 
                    onChange={(e) => setVolume(Number(e.target.value))}
                    className="w-full h-1.5 bg-white/10 rounded-full accent-[var(--color-primary)] cursor-pointer appearance-none transition-all hover:h-2"
                  />
                )}
              </div>
              {mode === 'voice' && <span className="hidden sm:inline text-[10px] text-zinc-500 font-mono w-6 text-right">{volume}</span>}
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

            {/* ZONE 3: System Controls */}
            <div className={`flex items-center gap-2 flex-1 ${mode === 'voice' ? 'justify-end' : 'flex-col'}`}>
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
          <form onSubmit={handleChatSubmit} className="fixed bottom-24 left-1/2 -translate-x-1/2 w-full max-w-3xl px-4 z-50 transition-all duration-500 pl-4 md:pl-24">
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

      {/* Transcript Panel (Right Side - Voice mode only) */}
      {mode === 'voice' && <TranscriptPanel />}

      {/* Settings Overlay */}
      <SettingsPanel />
      <MCPPanel />
    </div>
  );
}

export default App;
