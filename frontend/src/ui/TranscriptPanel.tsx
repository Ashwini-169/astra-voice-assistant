import React, { useEffect, useRef } from 'react';
import { useAgentStore } from '../core/state/agentStore';

/**
 * Minimal markdown renderer — handles bold, italic, code, links, lists, headings.
 * No external dependencies required.
 */
function renderMarkdown(text: string): string {
  let html = text
    // Escape HTML entities
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    // Code blocks (``` ... ```)
    .replace(/```(\w*)\n?([\s\S]*?)```/g, '<pre class="bg-zinc-900/80 rounded-lg p-3 my-2 text-xs font-mono overflow-x-auto border border-white/5"><code>$2</code></pre>')
    // Inline code
    .replace(/`([^`]+)`/g, '<code class="bg-zinc-800 rounded px-1.5 py-0.5 text-xs font-mono text-[var(--color-primary)]">$1</code>')
    // Bold
    .replace(/\*\*(.*?)\*\*/g, '<strong class="font-semibold text-white">$1</strong>')
    // Italic
    .replace(/\*(.*?)\*/g, '<em>$1</em>')
    // Headings
    .replace(/^### (.*$)/gim, '<h3 class="text-sm font-semibold text-white mt-3 mb-1">$1</h3>')
    .replace(/^## (.*$)/gim, '<h2 class="text-base font-semibold text-white mt-3 mb-1">$1</h2>')
    .replace(/^# (.*$)/gim, '<h1 class="text-lg font-bold text-white mt-3 mb-1">$1</h1>')
    // Unordered lists
    .replace(/^\s*[-*+]\s+(.*$)/gim, '<li class="ml-4 list-disc text-zinc-200">$1</li>')
    // Numbered lists
    .replace(/^\s*\d+\.\s+(.*$)/gim, '<li class="ml-4 list-decimal text-zinc-200">$1</li>')
    // Line breaks → <br> (but not inside code blocks)
    .replace(/\n/g, '<br/>');

  // Wrap consecutive <li> elements in <ul>
  html = html.replace(/((<li[^>]*>.*?<\/li>(<br\/>)?)+)/g, '<ul class="my-1">$1</ul>');
  // Clean up <br/> inside <ul>
  html = html.replace(/<br\/>\s*<\/ul>/g, '</ul>');
  html = html.replace(/<\/li><br\/>/g, '</li>');

  return html;
}

export const TranscriptPanel: React.FC = () => {
  const { chatHistory, state, partialTranscript, response } = useAgentStore();
  const scrollRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [chatHistory, response, partialTranscript]);

  const hasContent = chatHistory.length > 0 || partialTranscript || response;
  if (!hasContent) return null;

  return (
    <div className="fixed right-0 top-0 h-full w-96 z-40 hidden md:flex flex-col bg-zinc-950/60 backdrop-blur-xl border-l border-white/5">
      {/* Header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-white/5">
        <span className="material-symbols-outlined text-[var(--color-primary)] text-lg">forum</span>
        <h2 className="text-sm font-manrope font-semibold text-white uppercase tracking-wider">Conversation</h2>
        <span className="ml-auto text-[10px] text-zinc-500">{chatHistory.length} messages</span>
      </div>

      {/* Chat Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4 custom-scrollbar">
        
        {chatHistory.map((msg) => (
          <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
            <div className={`max-w-[85%] rounded-2xl px-4 py-3 shadow-lg ${
              msg.role === 'user' 
                ? 'bg-zinc-800/80 rounded-tr-sm border border-white/5' 
                : msg.role === 'tool'
                ? 'bg-amber-900/20 rounded-tl-sm border border-amber-500/20'
                : 'bg-[var(--color-primary)]/8 rounded-tl-sm border border-[var(--color-primary)]/15'
            }`}>
              {/* Role Label */}
              <div className="flex items-center gap-1.5 mb-1">
                {msg.role === 'assistant' && (
                  <span className="material-symbols-outlined text-[var(--color-primary)] text-xs">smart_toy</span>
                )}
                {msg.role === 'tool' && (
                  <span className="material-symbols-outlined text-amber-400 text-xs">build</span>
                )}
                <p className={`text-[10px] uppercase tracking-wider font-manrope ${
                  msg.role === 'user' ? 'text-zinc-400 text-right w-full' 
                  : msg.role === 'tool' ? 'text-amber-400'
                  : 'text-[var(--color-primary)]'
                }`}>
                  {msg.role === 'user' ? 'You' : msg.role === 'tool' ? `Tool: ${msg.toolName || 'unknown'}` : 'Astra'}
                </p>
              </div>
              {/* Content */}
              {msg.role === 'user' ? (
                <p className="text-sm text-zinc-100 leading-relaxed">{msg.content}</p>
              ) : (
                <div 
                  className="text-sm text-zinc-200 leading-relaxed prose-invert"
                  dangerouslySetInnerHTML={{ __html: renderMarkdown(msg.content) }}
                />
              )}
            </div>
          </div>
        ))}

        {/* Live Streaming Response (not yet in history) */}
        {response && state === 'speaking' && (
          <div className="flex justify-start">
            <div className="max-w-[85%] bg-[var(--color-primary)]/8 rounded-2xl rounded-tl-sm px-4 py-3 border border-[var(--color-primary)]/15 shadow-lg">
              <div className="flex items-center gap-1.5 mb-1">
                <span className="material-symbols-outlined text-[var(--color-primary)] text-xs">smart_toy</span>
                <p className="text-[10px] text-[var(--color-primary)] uppercase tracking-wider font-manrope">Astra</p>
                <span className="ml-2 w-1.5 h-1.5 rounded-full bg-[var(--color-primary)] animate-pulse"></span>
              </div>
              <div 
                className="text-sm text-zinc-200 leading-relaxed prose-invert"
                dangerouslySetInnerHTML={{ __html: renderMarkdown(response) }}
              />
            </div>
          </div>
        )}

        {/* Thinking Indicator */}
        {state === 'thinking' && (
          <div className="flex justify-start">
            <div className="max-w-[85%] bg-amber-900/10 rounded-2xl rounded-tl-sm px-4 py-3 border border-amber-500/15 shadow-lg">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-amber-400 text-sm animate-spin">progress_activity</span>
                <p className="text-xs text-amber-300 font-manrope">Thinking...</p>
              </div>
            </div>
          </div>
        )}

        {/* Partial Transcript (interim ASR — user is still speaking) */}
        {partialTranscript && state === 'listening' && (
          <div className="flex justify-end">
            <div className="max-w-[85%] bg-zinc-800/50 rounded-2xl rounded-tr-sm px-4 py-3 border border-white/3 shadow-lg">
              <p className="text-[10px] text-zinc-500 uppercase tracking-wider text-right mb-1 font-manrope">You (listening...)</p>
              <p className="text-sm text-zinc-400 italic leading-relaxed">{partialTranscript}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
