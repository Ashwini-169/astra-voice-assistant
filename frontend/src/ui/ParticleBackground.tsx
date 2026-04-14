import React, { useEffect, useRef } from 'react';
import { useAgentStore } from '../core/state/agentStore';

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  size: number;
  alpha: number;
  layer: number; // 0: far, 1: mid, 2: near
  burstTimer: number;
}

const MAX_PARTICLES = 100;
const CENTER_FALLOFF_RADIUS = 180;
const DAMPING = 0.95;
const MAX_VELOCITY = 4;

export const ParticleBackground: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const particlesRef = useRef<Particle[]>([]);
  const stateRef = useRef<string>('idle');
  const animationFrameRef = useRef<number>(0);
  
  const { state, micRms, playbackRms } = useAgentStore();

  // Keep state sync for the render loop
  useEffect(() => {
    // Detect burst entry
    if (state === 'interrupting' && stateRef.current !== 'interrupting') {
      triggerBurst();
    }
    stateRef.current = state;
  }, [state]);

  const triggerBurst = () => {
    particlesRef.current.forEach(p => {
      const dx = p.x - window.innerWidth / 2;
      const dy = p.y - window.innerHeight / 2;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const force = (Math.random() * 20 + 10);
      p.vx += (dx / dist) * force;
      p.vy += (dy / dist) * force;
      p.burstTimer = 1.0;
    });
  };

  const initParticles = (width: number, height: number) => {
    const particles: Particle[] = [];
    for (let i = 0; i < MAX_PARTICLES; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        size: Math.random() * 2 + 1,
        alpha: Math.random() * 0.5 + 0.1,
        layer: Math.floor(Math.random() * 3),
        burstTimer: 0,
      });
    }
    particlesRef.current = particles;
  };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const handleResize = () => {
      const dpr = window.devicePixelRatio || 1;
      canvas.width = window.innerWidth * dpr;
      canvas.height = window.innerHeight * dpr;
      canvas.style.width = `${window.innerWidth}px`;
      canvas.style.height = `${window.innerHeight}px`;
      ctx.scale(dpr, dpr);
      
      if (particlesRef.current.length === 0) {
        initParticles(window.innerWidth, window.innerHeight);
      }
    };

    handleResize();
    window.addEventListener('resize', handleResize);

    let lastTime = performance.now();

    const render = (time: number) => {
      const dt = (time - lastTime) / 16.66; // Normalized delta
      lastTime = time;

      ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

      const centerX = window.innerWidth / 2;
      const centerY = window.innerHeight / 2;
      const currentState = stateRef.current;
      
      // Determine effective energy based on state routing
      const energy = currentState === 'listening' ? micRms : 
                     currentState === 'speaking' ? playbackRms : 0;

      particlesRef.current.forEach(p => {
        // 1. Motion Logic based on States
        const dx = p.x - centerX;
        const dy = p.y - centerY;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;

        if (currentState === 'listening') {
          // Central Attraction
          const pull = 0.05 * dt;
          p.vx -= (dx / dist) * pull;
          p.vy -= (dy / dist) * pull;
        } else if (currentState === 'thinking') {
          // Swirl / Vortex
          const angle = Math.atan2(dy, dx);
          const swirlForce = 0.2 * dt;
          p.vx += Math.cos(angle + Math.PI / 2) * swirlForce;
          p.vy += Math.sin(angle + Math.PI / 2) * swirlForce;
          // Add a bit of noise
          p.vx += (Math.random() - 0.5) * 0.1;
          p.vy += (Math.random() - 0.5) * 0.1;
        } else if (currentState === 'speaking') {
          // Expansion Feel
          const push = 0.08 * dt * (1 + energy * 2);
          p.vx += (dx / dist) * push;
          p.vy += (dy / dist) * push;
        }

        // Apply Damping & Layer Speed (Parallax)
        const speedMultiplier = (p.layer + 1) * 0.4;
        p.vx *= DAMPING;
        p.vy *= DAMPING;
        
        // Idle Drift
        p.vx += (Math.random() - 0.5) * 0.02 * speedMultiplier;
        p.vy += (Math.random() - 0.5) * 0.02 * speedMultiplier;

        // Cap Velocity
        const vSq = p.vx * p.vx + p.vy * p.vy;
        if (vSq > MAX_VELOCITY * MAX_VELOCITY) {
          const v = Math.sqrt(vSq);
          p.vx = (p.vx / v) * MAX_VELOCITY;
          p.vy = (p.vy / v) * MAX_VELOCITY;
        }

        // Move
        p.x += p.vx * dt;
        p.y += p.vy * dt;

        // Wrap Around
        if (p.x < 0) p.x = window.innerWidth;
        if (p.x > window.innerWidth) p.x = 0;
        if (p.y < 0) p.y = window.innerHeight;
        if (p.y > window.innerHeight) p.y = 0;

        // 2. Rendering logic
        // Radial Falloff (Clean center)
        let opacityMult = 1;
        if (dist < CENTER_FALLOFF_RADIUS) {
          opacityMult = Math.max(0, (dist - 100) / (CENTER_FALLOFF_RADIUS - 100));
        }

        // Audio Reactivity (size/opacity only)
        const dynamicAlpha = p.alpha * opacityMult * (1 + energy * 0.5);
        const dynamicSize = p.size * (1 + energy * 0.3);

        ctx.beginPath();
        ctx.arc(p.x, p.y, dynamicSize, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(153, 247, 255, ${dynamicAlpha})`; // Use primary cyan tint
        ctx.fill();

        // Burst decay
        if (p.burstTimer > 0) p.burstTimer -= 0.02 * dt;
      });

      animationFrameRef.current = requestAnimationFrame(render);
    };

    animationFrameRef.current = requestAnimationFrame(render);

    return () => {
      window.removeEventListener('resize', handleResize);
      cancelAnimationFrame(animationFrameRef.current);
    };
  }, [micRms, playbackRms]);

  return (
    <canvas 
      ref={canvasRef} 
      className="fixed inset-0 pointer-events-none z-0 opacity-60"
      style={{ mixBlendMode: 'screen' }}
    />
  );
};
