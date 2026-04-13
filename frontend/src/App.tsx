import { Sidebar } from './ui/Sidebar';
import { Avatar } from './ui/Avatar';
import { Waveform } from './ui/Waveform';
import { useVoicePipeline } from './hooks/useVoicePipeline';
import { useAgentStore } from './core/state/agentStore';
import { audioPlayer } from './core/audio/player';

const STATE_LABELS: Record<string, string> = {
  idle: 'System Idle',
  listening: '🎙️ Listening...',
  thinking: '🧠 Thinking...',
  speaking: '🔊 Speaking...',
  interrupting: '⏹️ Interrupting...',
};

function App() {
  const { startPipeline, stopPipeline } = useVoicePipeline();
  const { state, transcript, response } = useAgentStore();

  const handleMicClick = () => {
    audioPlayer.resume();
    if (state === 'idle') {
      startPipeline();
    } else {
      stopPipeline();
    }
  };

  return (
    <div className="bg-surface text-on-surface min-h-screen overflow-hidden selection:bg-primary/30">
      
      {/* TopNavBar */}
      <header className="flex justify-between items-center px-8 py-6 w-full fixed top-0 z-50 bg-transparent">
        <div className="text-xl font-bold tracking-tighter text-white font-manrope">Ethereal AI</div>
        <nav className="hidden md:flex items-center gap-8 text-sm font-manrope tracking-tight">
          <a className="text-white font-semibold hover:text-indigo-300 transition-colors" href="#">Astra</a>
          <a className="text-zinc-500 hover:text-indigo-300 transition-colors" href="#">Manifest</a>
          <a className="text-zinc-500 hover:text-indigo-300 transition-colors" href="#">Archive</a>
        </nav>
        <div className="flex items-center gap-4">
          <button className="text-zinc-500 hover:text-indigo-300 transition-colors">
            <span className="material-symbols-outlined mt-1">history</span>
          </button>
          <button className="text-zinc-500 hover:text-indigo-300 transition-colors">
            <span className="material-symbols-outlined mt-1">settings</span>
          </button>
          <div className="w-8 h-8 rounded-full overflow-hidden bg-[var(--color-surface-container-highest)]">
            <img alt="User profile" className="w-full h-full object-cover" src="https://lh3.googleusercontent.com/aida-public/AB6AXuCVuSrXba7Q1F6ClOedQss2Tr3Wfz5Y9Tq7ozqhMVKb45io_UO8y6yDJnc96LUivzA4ANftcrm_wxT7YAv728sTM9-91-VZ_X5hsRuYyvWtPMiTdK-7egJq4WEgENAkS8FeSBswhaNgAcYXJYpPPR6P9nwzqD9nik10j6rWgxtGtohYZmpYUDswwgFeBkvXntfTrnvUf-fSbWADQ5CFjuAE_3F2TgJ1IjrjpBt97ysFjTNJeDvpMhzJn62nQ_Uc5q2YSn8-vhLu5eg4"/>
          </div>
        </div>
      </header>

      <Sidebar />

      <main className="relative h-screen w-full flex flex-col items-center p-6 bg-[var(--color-surface)] overflow-hidden">
        {/* Environment Background Layer */}
        <div className="absolute inset-0 z-0 pointer-events-none">
          <div className="wave-container">
            <svg className="wave wave-1" preserveAspectRatio="none" viewBox="0 0 1440 320" xmlns="http://www.w3.org/2000/svg">
                <path d="M0,192L48,197.3C96,203,192,213,288,229.3C384,245,480,267,576,250.7C672,235,768,181,864,181.3C960,181,1056,235,1152,234.7C1248,235,1344,181,1392,154.7L1440,128L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z" fill="#99f7ff" fillOpacity="0.1"></path>
            </svg>
            <svg className="wave wave-2" preserveAspectRatio="none" viewBox="0 0 1440 320" xmlns="http://www.w3.org/2000/svg">
                <path d="M0,64L48,80C96,96,192,128,288,128C384,128,480,96,576,106.7C672,117,768,171,864,176C960,181,1056,139,1152,122.7C1248,107,1344,117,1392,122.7L1440,128L1440,320L1392,320C1344,320,1248,320,1152,320C1056,320,960,320,864,320C768,320,672,320,576,320C480,320,384,320,288,320C192,320,96,320,48,320L0,320Z" fill="#ec63ff" fillOpacity="0.1"></path>
            </svg>
          </div>
          <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-[var(--color-primary)]/5 rounded-full blur-[120px]"></div>
          <div className="absolute bottom-1/4 right-1/4 w-[500px] h-[500px] bg-[var(--color-tertiary)]/5 rounded-full blur-[150px]"></div>
          <div className="absolute inset-0 bg-[url('https://www.transparenttextures.com/patterns/stardust.png')] opacity-10 mix-blend-screen"></div>
        </div>

        <Avatar />

        {/* Floating UI Widgets */}
        <div className="absolute inset-0 pointer-events-none flex items-center justify-center">
            {/* Sentiment Gauge */}
            <div className="absolute left-10 md:left-40 top-1/2 -translate-y-1/2 pointer-events-auto hidden lg:block">
                <div className="glass-panel p-6 rounded-xl border border-white/5 w-48 shadow-2xl">
                    <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-4">Neural Harmony</p>
                    <div className="h-1 w-full bg-[var(--color-surface-container-highest)] rounded-full overflow-hidden">
                        <div className="h-full w-4/5 bg-gradient-to-r from-primary to-secondary"></div>
                    </div>
                    <div className="flex justify-between mt-2">
                        <span className="text-[10px] text-[var(--color-primary)]">88%</span>
                        <span className="text-[10px] text-zinc-400">Optimal</span>
                    </div>
                </div>
            </div>

            {/* Active Protocol */}
            <div className="absolute right-10 md:right-40 top-1/2 -translate-y-1/2 pointer-events-auto hidden lg:block">
                <div className="glass-panel p-6 rounded-xl border border-white/5 w-56 shadow-2xl">
                    <div className="flex items-center gap-3 mb-3">
                        <span className="material-symbols-outlined text-[var(--color-secondary)] text-lg">auto_awesome</span>
                        <p className="text-[10px] text-zinc-400 uppercase tracking-widest">Synthesis Mode</p>
                    </div>
                    <h3 className="text-lg font-manrope text-white font-semibold leading-tight">Abstract Reality Reconstruction</h3>
                </div>
            </div>
        </div>

        <Waveform />

        {/* Live Transcript & Response Panel */}
        <div className="fixed bottom-32 left-1/2 -translate-x-1/2 w-full max-w-lg px-6 z-40 pointer-events-none">
          {/* State Badge */}
          <div className="flex justify-center mb-3">
            <div className={`px-4 py-1.5 rounded-full text-xs font-manrope tracking-wide backdrop-blur-md border transition-all duration-300 ${
              state === 'idle' 
                ? 'bg-zinc-900/60 border-zinc-700/30 text-zinc-400' 
                : state === 'listening' 
                ? 'bg-emerald-900/30 border-emerald-500/30 text-emerald-300 animate-pulse' 
                : state === 'thinking'
                ? 'bg-amber-900/30 border-amber-500/30 text-amber-300 animate-pulse'
                : state === 'speaking'
                ? 'bg-indigo-900/30 border-indigo-500/30 text-indigo-300'
                : 'bg-red-900/30 border-red-500/30 text-red-300'
            }`}>
              {STATE_LABELS[state] || state}
            </div>
          </div>

          {/* Transcript */}
          {transcript && (
            <div className="glass-panel rounded-xl px-5 py-3 mb-2 border border-white/5">
              <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-1">You</p>
              <p className="text-sm text-zinc-200 font-inter leading-relaxed">{transcript}</p>
            </div>
          )}

          {/* Response */}
          {response && (
            <div className="glass-panel rounded-xl px-5 py-3 border border-indigo-500/10 max-h-32 overflow-y-auto">
              <p className="text-[10px] text-indigo-400 uppercase tracking-widest mb-1">Astra</p>
              <p className="text-sm text-zinc-100 font-inter leading-relaxed">{response}</p>
            </div>
          )}
        </div>

        {/* Bottom Controls */}
        <div className="fixed bottom-0 left-1/2 -translate-x-1/2 w-full px-4 pb-8 flex flex-col items-center z-50 md:max-w-xl">
          <div className="bg-zinc-950/20 backdrop-blur-md rounded-full px-8 py-4 flex items-center gap-8 shadow-2xl shadow-indigo-500/10 border border-white/5">
            <button className="flex flex-col items-center justify-center text-zinc-500 hover:text-white transition-colors">
              <span className="material-symbols-outlined mt-1">terminal</span>
            </button>
            <button 
              className="group relative" 
              onClick={handleMicClick}
            >
              <div className={`absolute -inset-4 bg-[var(--color-primary)]/20 rounded-full blur-xl transition-all ${state !== 'idle' ? 'bg-[var(--color-primary)]/50' : 'group-hover:bg-[var(--color-primary)]/40'}`}></div>
              <div className="w-16 h-16 rounded-full bg-gradient-to-br from-[#ac8aff] to-[#99f7ff] flex items-center justify-center relative z-10 shadow-lg shadow-[var(--color-primary)]/20 group-active:scale-95 transition-transform">
                <span className="material-symbols-outlined text-[#005f64] text-3xl" style={{ fontVariationSettings: "'FILL' 1" }}>
                  {state !== 'idle' ? 'stop' : 'mic'}
                </span>
              </div>
            </button>
            <button className="flex flex-col items-center justify-center text-zinc-500 hover:text-white transition-colors">
              <span className="material-symbols-outlined mt-1">flare</span>
            </button>
          </div>
        </div>

      </main>

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
