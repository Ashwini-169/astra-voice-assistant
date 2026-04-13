import React, { useEffect, useRef } from 'react';
import { useAgentStore } from '../core/state/agentStore';

export const Avatar: React.FC = () => {
  const { state } = useAgentStore();
  
  const containerRef = useRef<HTMLDivElement>(null);
  const faceRef = useRef<HTMLDivElement>(null);
  const orbRef = useRef<HTMLDivElement>(null);
  
  const isSpeaking = state === 'speaking';

  useEffect(() => {
    let animationFrameId: number;
    let mouseX = window.innerWidth / 2;
    let mouseY = window.innerHeight / 2;

    const handleMouseMove = (e: MouseEvent) => {
      mouseX = e.clientX;
      mouseY = e.clientY;
    };

    window.addEventListener('mousemove', handleMouseMove);

    const updateAstraGaze = () => {
      if (!containerRef.current || !faceRef.current || !orbRef.current) return;
      
      const rect = containerRef.current.getBoundingClientRect();
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      
      const deltaX = (mouseX - centerX) / window.innerWidth;
      const deltaY = (mouseY - centerY) / window.innerHeight;
      
      containerRef.current.style.transform = `rotateY(${deltaX * 15}deg) rotateX(${-deltaY * 15}deg)`;
      faceRef.current.style.transform = `translateX(${deltaX * 40}px) translateY(${deltaY * 40}px)`;
      orbRef.current.style.boxShadow = `${-deltaX * 30}px ${-deltaY * 30}px 80px rgba(153,247,255,0.2)`;
      
      animationFrameId = requestAnimationFrame(updateAstraGaze);
    };

    updateAstraGaze();

    return () => {
      window.removeEventListener('mousemove', handleMouseMove);
      cancelAnimationFrame(animationFrameId);
    };
  }, []);

  return (
    <div className={`relative z-10 flex flex-col items-center bg-transparent mt-24 md:mt-32 ${isSpeaking ? 'is-speaking' : ''}`} ref={containerRef} id="astra-container">
      {/* Aura & Halo */}
      <div className={`absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[450px] h-[450px] energy-halo rounded-full opacity-60 ${state === 'listening' ? 'animate-pulse' : ''}`}></div>
      
      {/* The Core Orb */}
      <div 
        ref={orbRef}
        id="astra-orb"
        className="relative group cursor-pointer bg-transparent"
      >
        <div className="w-64 h-64 rounded-full orb-gradient relative z-20 shadow-[0_0_80px_rgba(153,247,255,0.2)] transition-all duration-700 group-hover:shadow-[0_0_120px_rgba(236,99,255,0.4)] group-hover:scale-105 flex flex-col items-center justify-center">
          
          <div className="absolute inset-0 bg-gradient-to-tr from-white/20 to-transparent opacity-40"></div>
          
          {/* Astra Face Group for Gaze */}
          <div className="flex flex-col items-center justify-center" id="astra-face" ref={faceRef}>
            {/* Cyan Glowing Eyes - Vertical, wider spacing */}
            <div className="flex relative z-30 transition-transform duration-300 translate-y-1 gap-12">
              <div className="w-3 h-8 bg-[var(--color-primary-container)] rounded-full shadow-[0_0_15px_#00f1fe] animate-pulse animate-blink"></div>
              <div className="w-3 h-8 bg-[var(--color-primary-container)] rounded-full shadow-[0_0_15px_#00f1fe] animate-pulse animate-blink"></div>
            </div>
            
            {/* Speaking State: Minimal Mouth */}
            <div className="mt-8 relative z-30">
              <div className="orb-mouth"></div>
            </div>
          </div>
          
          {/* Inner Refraction Elements */}
          <div className="absolute -bottom-10 -right-10 w-40 h-40 bg-white/10 blur-2xl rounded-full"></div>
          <div className="absolute inset-0 z-40">
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-primary)] text-[10px]" style={{top: '20%', left: '20%', animationDelay: '0s'}}>star</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-secondary)] text-[8px]" style={{top: '30%', left: '70%', animationDelay: '1s'}}>favorite</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-tertiary)] text-[12px]" style={{top: '60%', left: '80%', animationDelay: '2s'}}>star</span>
            <span className="cheerful-particle material-symbols-outlined text-[var(--color-primary)] text-[8px]" style={{top: '70%', left: '15%', animationDelay: '0.5s'}}>favorite</span>
          </div>
        </div>
        {/* Floor Shadow/Reflection */}
        <div className="mt-16 w-32 h-4 bg-zinc-950/60 blur-xl rounded-full scale-x-150 mx-auto opacity-50"></div>
      </div>
      
      {/* Character Title & Status */}
      <div className="mt-12 text-center">
        <h1 className="text-5xl md:text-7xl font-extrabold tracking-tighter text-white mb-2">Astra</h1>
        <div className="flex items-center justify-center gap-3">
          <span className={`w-1.5 h-1.5 rounded-full ${state === 'idle' ? 'bg-gray-500' : 'bg-[var(--color-primary)] animate-ping'}`}></span>
          <p className="text-[var(--color-on-surface-variant)] font-label tracking-[0.2em] uppercase text-xs">
            {state === 'idle' ? 'System Idle' : 'Consciousness active'}
          </p>
        </div>
      </div>
    </div>
  );
};
