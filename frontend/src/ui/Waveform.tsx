import React, { useEffect, useRef } from 'react';
import { useAgentStore } from '../core/state/agentStore';

class Wave {
    frequency: number;
    amplitude: number;
    speed: number;
    opacity: number;
    offset: number;
    color: string;
    phase: number;
    currentAmplitude?: number;

    constructor(frequency: number, amplitude: number, speed: number, opacity: number, offset: number, color?: string) {
        this.frequency = frequency;
        this.amplitude = amplitude;
        this.speed = speed;
        this.opacity = opacity;
        this.offset = offset;
        this.color = color || '99, 102, 241';
        this.phase = 0;
    }

    draw(context: CanvasRenderingContext2D, canvasWidth: number, canvasHeight: number, anchorFactor = 1, isCentered = false) {
        context.beginPath();
        context.lineWidth = 1.5;
        context.strokeStyle = `rgba(${this.color}, ${this.opacity})`;
        for (let x = 0; x <= canvasWidth; x += 2) {
            const normalizedX = (x / canvasWidth) * 2 - 1;
            const anchoring = isCentered ? Math.pow(Math.cos(normalizedX * Math.PI / 2), 4) : Math.pow(Math.cos(normalizedX * Math.PI / 2), anchorFactor);
            const y = (canvasHeight / 2) + 
                      (Math.sin(x * this.frequency + this.phase + this.offset) * this.amplitude * anchoring) - 
                      (Math.cos(x * this.frequency * 0.5 + this.phase) * (this.amplitude * 0.5) * anchoring);
            if (x === 0) context.moveTo(x, y);
            else context.lineTo(x, y);
        }
        context.stroke();
        this.phase += this.speed;
    }
}

export const Waveform: React.FC = () => {
    const { state } = useAgentStore();
    const bgCanvasRef = useRef<HTMLCanvasElement>(null);
    const fgCanvasRef = useRef<HTMLCanvasElement>(null);

    const isSpeaking = state === 'speaking' || state === 'listening';

    useEffect(() => {
        const bgCanvas = bgCanvasRef.current;
        const fgCanvas = fgCanvasRef.current;
        if (!bgCanvas || !fgCanvas) return;

        const ctx = bgCanvas.getContext('2d');
        const lCtx = fgCanvas.getContext('2d');
        if (!ctx || !lCtx) return;

        let animationFrameId: number;

        const bottomWaves = [
            new Wave(0.005, 60, 0.02, 0.3, 0),
            new Wave(0.008, 40, -0.015, 0.15, Math.PI / 4),
            new Wave(0.012, 25, 0.03, 0.1, Math.PI / 2)
        ];

        const synthWaves = [
            new Wave(0.01, 70, 0.05, 0.6, 0, '153, 247, 255'), 
            new Wave(0.015, 55, 0.04, 0.4, Math.PI/3, '172, 138, 255'), 
            new Wave(0.008, 80, 0.03, 0.3, Math.PI/1.5, '236, 99, 255'), 
            new Wave(0.02, 45, 0.06, 0.2, Math.PI, '0, 241, 254') 
        ];

        const resize = () => {
            bgCanvas.width = window.innerWidth;
            bgCanvas.height = window.innerHeight;
            fgCanvas.width = window.innerWidth;
            fgCanvas.height = 240;
        };

        window.addEventListener('resize', resize);
        resize();

        const drawCenteredWave = (context: CanvasRenderingContext2D, canvasWidth: number, canvasHeight: number, wave: any) => {
            context.beginPath();
            context.lineWidth = 2;
            context.strokeStyle = `rgba(${wave.color}, ${wave.opacity})`;
            
            for (let x = 0; x <= canvasWidth; x += 4) {
                const normalizedX = (x / canvasWidth) * 2 - 1; 
                const anchoring = Math.pow(Math.cos(normalizedX * Math.PI / 2), 6);
                const y = (canvasHeight / 2) + 
                          (Math.sin(x * wave.frequency + wave.phase + wave.offset) * wave.amplitude * anchoring) +
                          (Math.cos(x * wave.frequency * 0.7 + wave.phase) * (wave.amplitude * 0.4) * anchoring);
                if (x === 0) context.moveTo(x, y);
                else context.lineTo(x, y);
            }
            context.stroke();
        };

        const animate = () => {
            ctx.clearRect(0, 0, bgCanvas.width, bgCanvas.height);
            bottomWaves.forEach(wave => {
                ctx.save();
                ctx.translate(0, bgCanvas.height - 40);
                wave.draw(ctx, bgCanvas.width, 80, 0.5);
                ctx.restore();
            });

            lCtx.clearRect(0, 0, fgCanvas.width, fgCanvas.height);
            synthWaves.forEach(wave => {
                const targetAmp = isSpeaking ? wave.amplitude * 1.5 : wave.amplitude * 0.3;
                const currentAmp = wave.currentAmplitude || targetAmp;
                wave.currentAmplitude = currentAmp + (targetAmp - currentAmp) * 0.1;
                
                const tempWave = {...wave, amplitude: wave.currentAmplitude};
                
                lCtx.save();
                lCtx.translate(0, fgCanvas.height - 80); 
                drawCenteredWave(lCtx, fgCanvas.width, 160, tempWave);
                lCtx.restore();
                
                wave.phase += wave.speed;
            });

            animationFrameId = requestAnimationFrame(animate);
        };

        animate();

        return () => {
            window.removeEventListener('resize', resize);
            cancelAnimationFrame(animationFrameId);
        };
    }, [isSpeaking]);

    return (
        <>
            <canvas ref={fgCanvasRef} id="listening-canvas" className="absolute bottom-0 left-1/2 -translate-x-1/2 z-15 pointer-events-none w-screen h-[240px]"></canvas>
            <canvas ref={bgCanvasRef} className="fixed inset-0 -z-10 w-full h-full pointer-events-none" id="waveformCanvas"></canvas>
        </>
    );
};
