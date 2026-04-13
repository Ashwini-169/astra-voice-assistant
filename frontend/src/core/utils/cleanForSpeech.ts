/**
 * Clean text for TTS speech output.
 * Strips markdown formatting, symbols, and artifacts that sound bad
 * when read aloud, while preserving the original text in the chat UI.
 */
export function cleanForSpeech(text: string): string {
  let cleaned = text;

  // Remove markdown bold/italic markers: **text** → text, *text* → text, __text__ → text
  cleaned = cleaned.replace(/\*{1,3}(.*?)\*{1,3}/g, '$1');
  cleaned = cleaned.replace(/_{1,3}(.*?)_{1,3}/g, '$1');

  // Remove markdown headings: ## Heading → Heading
  cleaned = cleaned.replace(/^#{1,6}\s*/gm, '');

  // Remove markdown links: [text](url) → text
  cleaned = cleaned.replace(/\[([^\]]*)\]\([^)]*\)/g, '$1');

  // Remove inline code backticks: `code` → code
  cleaned = cleaned.replace(/`{1,3}([^`]*)`{1,3}/g, '$1');

  // Remove markdown list bullets: - item → item, * item → item
  cleaned = cleaned.replace(/^\s*[-*+]\s+/gm, '');

  // Remove numbered list prefixes: 1. item → item  (but keep the text)
  cleaned = cleaned.replace(/^\s*\d+\.\s+/gm, '');

  // Remove blockquote markers: > text → text
  cleaned = cleaned.replace(/^\s*>\s*/gm, '');

  // Remove horizontal rules: --- or *** or ___
  cleaned = cleaned.replace(/^[-*_]{3,}\s*$/gm, '');

  // Remove emoji shortcodes: :emoji_name:
  cleaned = cleaned.replace(/:[a-zA-Z0-9_+-]+:/g, '');

  // Remove HTML tags
  cleaned = cleaned.replace(/<[^>]+>/g, '');

  // Remove remaining standalone special chars that shouldn't be spoken
  cleaned = cleaned.replace(/[#*_~`|]/g, '');

  // Collapse multiple spaces / newlines into single space
  cleaned = cleaned.replace(/\n+/g, '. ');
  cleaned = cleaned.replace(/\s{2,}/g, ' ');

  // Clean up awkward punctuation sequences
  cleaned = cleaned.replace(/\.\s*\.\s*/g, '. ');
  cleaned = cleaned.replace(/,\s*,/g, ',');

  return cleaned.trim();
}
