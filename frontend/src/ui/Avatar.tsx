import React, { useEffect, useRef } from 'react';
import { useAgentStore } from '../core/state/agentStore';

/**
 * Astra Avatar — Audio-Reactive AI Character
 *
 * State Behaviors:
 *   Listening  → Pulse orb scale with mic RMS
 *   Thinking   → Slow breathing glow
 *   Speaking   → Lip/mouth animation + waveform glow
 *   Interrupted→ Quick flicker
 *   Error      → Red pulse
 *   Idle       → Subtle ambient breathing
 */
export const Avatar: React.FC = () => {
  const { state, micRms, playbackRms, isAudioPlaying } = useAgentStore();
  
  const containerRef = useRef<HTMLDivElement>(null);
  const faceRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<HTMLDivElement>(null);
  const orbInnerRef = useRef<HTMLDivElement>(null);
  
  // The character strictly visually speaks when audio is outputting
  const isSpeaking = isAudioPlaying || state === 'speaking';
  const isListening = state === 'listening' && !isAudioPlaying;
  const isThinking = state === 'thinking' && !isAudioPlaying;
  const isInterrupted = state === 'interrupting';
  const isError = state === 'error';

  // Calculate dynamic mouth styles based on TTS playback RMS
  const mouthHeight = isSpeaking ? 2 + playbackRms * 12 : 4; 
  const mouthWidth = isSpeaking ? 12 + playbackRms * 16 : 24;
  const mouthRadius = isSpeaking ? (playbackRms > 0.2 ? 4 : 99) : 99;

  useEffect(() => {
    let animationFrameId: number;
    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;

    const handleMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };

    window.addEventListener('mousemove', handleMouseMove);

    const updateAstra = () => {
      if (!containerRef.current || !faceRef.current || !orbRef.current) return;
      
      // Gaze logic
      const rect = containerRef.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      
      const deltaX = (mouseX - centerX) / window.innerWidth;
      const deltaY = (mouseY - centerY) / window.innerHeight;
      
      containerRef.current.style.transform = `rotateY(${deltaX * 15}deg) rotateX(${-deltaY * 15}deg)`;
      faceRef.current.style.transform = `translateX(${deltaX * 40}px) translateY(${deltaY * 40}px)`;
      
      // Gaze-reactive orb shadow
      orbRef.current.style.boxShadow = `rgba(0, 241, 254, 0.4) ${-deltaX * 8}px ${1.06532 + (-deltaY * 8)}px 20px`;

      animationFrameId = requestAnimationFrame(updateAstra);
    };

    updateAstra();

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  // Audio-reactive orb scale (listening state pulses with mic RMS)
  useEffect(() => {
    if (!orbInnerRef.current) return;
    
    if (isListening) {
      const scale = 1 + micRms * 0.15; // max 15% scale boost
      orbInnerRef.current.style.transform = `scale(${scale})`;
      orbInnerRef.current.style.transition = 'transform 0.08s ease-out';
    } else if (isThinking) {
      orbInnerRef.current.style.transform = 'scale(1)';
      orbInnerRef.current.style.transition = 'transform 0.7s ease-in-out';
    } else if (isInterrupted) {
      orbInnerRef.current.style.transform = 'scale(0.95)';
      orbInnerRef.current.style.transition = 'transform 0.05s ease';
    } else {
      orbInnerRef.current.style.transform = 'scale(1)';
      orbInnerRef.current.style.transition = 'transform 0.5s ease';
    }
  }, [micRms, isListening, isThinking, isInterrupted]);

  // Determine state-based CSS classes
  const orbStateClass = isListening
    ? 'orb-listening'
    : isThinking
    ? 'orb-thinking'
    : isSpeaking
    ? 'orb-speaking'
    : isInterrupted
    ? 'orb-interrupted'
    : isError
    ? 'orb-error'
    : '';

  return (
    <div className={`relative z-10 flex flex-col items-center bg-transparent mt-24 md:mt-32 ${isSpeaking ? 'is-speaking' : ''}`} ref={containerRef} id="astra-container">
      
      {/* Background Aura Glow: Premium 330px radius with fluid animation */}
      <div className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[330px] h-[330px] rounded-full blur-[60px] pointer-events-none transition-all duration-1000 z-0 fluid-aura ${
        isSpeaking ? 'bg-[#99f7ff]/40 scale-110 opacity-60'
        : isListening ? 'bg-[#99f7ff]/20 scale-105 opacity-40'
        : isThinking ? 'bg-[#ac8aff]/20 scale-100 opacity-35'
        : isError ? 'bg-red-500/30 scale-100 opacity-50'
        : 'bg-[#99f7ff]/10 scale-95 opacity-30'
      }`} style={{ animation: 'breathe 8s infinite ease-in-out' }}></div>

      {/* Hura/Halo energy circle */}
      <div className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[390px] h-[390px] energy-halo rounded-full transition-opacity duration-700 ${
        isSpeaking ? 'opacity-30' : isListening ? 'opacity-70 animate-pulse' : 'opacity-20'
      } z-0`}></div>

      {/* Speak Gradient Ring */}
      {isSpeaking && (
        <div className="absolute top-1/2 left-1/2 w-80 h-80 pointer-events-none z-0">
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-full h-full rounded-full border-[3px] border-transparent ring-active"
               style={{ 
                 background: 'linear-gradient(var(--bg-app), var(--bg-app)) padding-box, linear-gradient(to right, #00f1fe, #ac8aff, #ec63ff) border-box',
                 filter: 'blur(2px)'
               }}
          ></div>
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[110%] h-[110%] rounded-full border-[1px] border-cyan-400/20 animate-ping opacity-20"></div>
        </div>
      )}

      {/* The Core Orb */}
      <div 
        ref={orbRef}
        id="astra-orb"
        className={`relative group cursor-pointer bg-transparent transition-all duration-700 rounded-full ${orbStateClass}`}
      >
        <div ref={orbInnerRef} className="w-64 h-64 rounded-full orb-gradient relative z-20 flex flex-col items-center justify-center group-hover:scale-105 transition-transform duration-700">
          
          {/* Gloss Overlay */}
          <div className="absolute inset-0 bg-gradient-to-tr from-white/20 to-transparent opacity-40 rounded-full"></div>
          
          {/* Astra Face Overlay */}
          <div className="flex flex-col items-center justify-center" id="astra-face" ref={faceRef}>
            {/* Cyan Glowing Eyes */}
            <div className="flex relative z-30 transition-transform duration-300 translate-y-1 gap-12">
              <div className={`w-3 h-8 bg-[var(--color-primary-container)] rounded-full shadow-[0_0_15px_#00f1fe] animate-blink ${isError ? 'bg-red-400 shadow-[0_0_15px_#ff4444]' : ''}`}></div>
              <div className={`w-3 h-8 bg-[var(--color-primary-container)] rounded-full shadow-[0_0_15px_#00f1fe] animate-blink ${isError ? 'bg-red-400 shadow-[0_0_15px_#ff4444]' : ''}`}></div>
            </div>
            
            {/* Speaking State: Minimal Mouth */}
            <div className="mt-8 relative z-30">
              <div 
                className="orb-mouth"
                style={{
                  height: `${mouthHeight}px`,
                  width: `${mouthWidth}px`,
                  borderRadius: `${mouthRadius}px`,
                  opacity: isSpeaking ? (0.6 + playbackRms * 0.4) : 0.3,
                  transition: 'height 0.05s ease-out, width 0.05s ease-out, border-radius 0.1s ease',
                }}
              ></div>
            </div>
          </div>
          
          {/* Inner Refraction Elements */}
          <div className="absolute -bottom-6 -right-6 w-32 h-32 bg-white/10 blur-2xl rounded-full"></div>
          <div className="absolute top-4 left-4 w-12 h-12 bg-white/20 blur-lg rounded-full"></div>
          
          {/* Cheerful Particles */}
          <div className="absolute inset-0 z-40 pointer-events-none">
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-primary)] text-[10px]" style={{top: '20%', left: '20%', animationDelay: '0s'}}>star</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-secondary)] text-[8px]" style={{top: '30%', left: '70%', animationDelay: '1s'}}>favorite</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-tertiary)] text-[12px]" style={{top: '60%', left: '80%', animationDelay: '2s'}}>star</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-primary)] text-[8px]" style={{top: '70%', left: '15%', animationDelay: '0.5s'}}>favorite</span>
          </div>
        </div>
      </div>
      
      {/* Character Title */}
      <div className="mt-12 text-center">
        <h1 className="text-5xl md:text-7xl font-extrabold tracking-tighter text-white mb-2">Astra</h1>
        
        {/* State Indicator below Astra */}
        <div className="flex items-center justify-center gap-3">
          <span className={`w-1.5 h-1.5 rounded-full animate-ping ${
            state === 'listening' ? 'bg-emerald-400' : 
            state === 'thinking' ? 'bg-amber-400' :
            state === 'speaking' ? 'bg-cyan-400' :
            state === 'error' ? 'bg-red-400' :
            'bg-zinc-500'
          }`}></span>
          <p className="text-[var(--color-on-surface-variant)] font-label tracking-[0.2em] uppercase text-xs">
            {state}
          </p>
        </div>
      </div>
    </div>
  );
};
