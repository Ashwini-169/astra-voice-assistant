"""Emotion tag parser for Fish-Speech / OpenAudio S1 Mini tag format.

Supported tag styles:
    (laughing)Hahaha...   →  EmotionSegment(emotion="laughing", text="Hahaha...")
    <shocked>Wait, what?  →  EmotionSegment(emotion="shocked",  text="Wait, what?")
    Plain text with no tag →  EmotionSegment(emotion=None,      text="Plain text...")

Full supported emotion set matches OpenAudio S1 Mini / Fish-Speech native tags.
"""
import re
from dataclasses import dataclass
from typing import List, Optional

# ── Canonical OpenAudio S1 Mini / Fish-Speech emotion set ────────────────────
KNOWN_EMOTIONS: frozenset = frozenset({
    "angry", "sad", "excited", "surprised", "satisfied", "delighted",
    "scared", "worried", "upset", "nervous", "frustrated", "depressed",
    "empathetic", "embarrassed", "disgusted", "moved", "proud", "relaxed",
    "grateful", "confident", "interested", "curious", "confused", "joyful",
    "laughing", "shocked", "whispering", "sigh", "sympathetic", "warm",
})

# Matches (any text) or <any text>  — captures group 1 or group 2
_TAG_RE = re.compile(r"\(([^)]{1,40})\)|<([^>]{1,40})>", re.IGNORECASE)

# ── Sentence-boundary regex for incremental flushing ────────────────────────
# Matches a sentence-ending punctuation followed by whitespace (or end-of-buf).
# Used by EmotionStreamBuffer to flush accumulated text at natural pauses even
# when no emotion tags are present.
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+')

# When the buffer exceeds this many characters without an emotion-tag flush,
# force a sentence-boundary flush so TTS starts speaking incrementally.
_SENTENCE_FLUSH_THRESHOLD = 80


# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class EmotionSegment:
    """A span of text with its associated emotion tag (or None for neutral)."""
    emotion: Optional[str]  # None means neutral / no tag
    text: str


def _normalize_emotion(raw: str) -> Optional[str]:
    """Map raw tag text to a canonical emotion name.

    Returns ``None`` for unknown tags so they are treated as plain text
    rather than being misinterpreted as emotions (e.g. "random access
    memory" or "solid state drives" from LLM output).

    Examples
    --------
    "laughing"                 → "laughing"
    "warm tone with light jolt" → "warm"
    "random access memory"     → None  (not a known emotion)
    """
    cleaned = raw.lower().strip()
    if cleaned in KNOWN_EMOTIONS:
        return cleaned
    # Partial match: "warm tone" → "warm", "deep sigh" → "sigh"
    for known in KNOWN_EMOTIONS:
        if known in cleaned:
            return known
    # Unknown tag — return None so it's treated as content, not emotion
    return None


def parse_emotion_segments(raw_text: str) -> List[EmotionSegment]:
    """Split raw LLM output into (emotion, text) segments.

    Parameters
    ----------
    raw_text : str
        LLM output possibly containing emotion tags such as:
        ``(laughing)Hahaha! That's so funny. (shocked)Wait—are you serious?``

    Returns
    -------
    List[EmotionSegment]
        Each segment contains the active emotion at that point + the spoken
        text fragment. The first segment may have ``emotion=None`` if the
        response starts without a tag.

    Examples
    --------
    >>> segs = parse_emotion_segments("(excited)Wow yaar! <sigh>Lekin...")
    >>> [(s.emotion, s.text) for s in segs]
    [('excited', 'Wow yaar!'), ('sigh', 'Lekin...')]
    """
    segments: List[EmotionSegment] = []
    buf = raw_text
    current_emotion: Optional[str] = None

    while True:
        match = _TAG_RE.search(buf)
        if not match:
            break
        pre = buf[: match.start()].strip()
        if pre:
            segments.append(EmotionSegment(emotion=current_emotion, text=pre))
        raw_emotion = match.group(1) or match.group(2)
        current_emotion = _normalize_emotion(raw_emotion)
        buf = buf[match.end():]

    tail = buf.strip()
    if tail:
        segments.append(EmotionSegment(emotion=current_emotion, text=tail))

    # No tags at all → single neutral segment
    if not segments:
        segments.append(EmotionSegment(emotion=None, text=raw_text.strip()))

    return segments


def strip_emotion_tags(raw_text: str) -> str:
    """Remove all emotion tags from text, returning clean spoken content.

    Example
    -------
    >>> strip_emotion_tags("(laughing)Hehehe! (shocked)What?!")
    'Hehehe! What?!'
    """
    return _TAG_RE.sub("", raw_text).strip()


def format_emotion_display(segments: List[EmotionSegment]) -> str:
    """Return a terminal-friendly string showing emotion labels inline.

    Example
    -------
    >>> format_emotion_display([EmotionSegment("laughing","Ha!"), EmotionSegment(None,"Ok.")])
    '[LAUGHING] Ha! | Ok.'
    """
    parts: List[str] = []
    for seg in segments:
        if seg.emotion:
            parts.append(f"[{seg.emotion.upper()}] {seg.text}")
        else:
            parts.append(seg.text)
    return " | ".join(parts)


# ── Streaming helper ──────────────────────────────────────────────────────────

class EmotionStreamBuffer:
    """Accumulate streaming LLM tokens and yield completed EmotionSegments.

    Detects emotion tag boundaries *as tokens arrive* so TTS can start each
    segment as soon as the tag closes — no need to wait for the full response.

    Usage::

        buf = EmotionStreamBuffer()
        async for token in llm_token_stream:
            for seg in buf.feed(token):
                await send_to_tts(seg.text, seg.emotion)
        for seg in buf.finish():
            await send_to_tts(seg.text, seg.emotion)
    """

    def __init__(self) -> None:
        self._buf: str = ""
        self._current_emotion: Optional[str] = None

    def feed(self, token: str) -> List[EmotionSegment]:
        """Feed one token; returns any segments that are now complete."""
        self._buf += token
        return self._flush()

    def finish(self) -> List[EmotionSegment]:
        """Flush remaining buffer at end of stream."""
        tail = self._buf.strip()
        self._buf = ""
        if tail:
            return [EmotionSegment(emotion=self._current_emotion, text=tail)]
        return []

    def _flush(self) -> List[EmotionSegment]:
        output: List[EmotionSegment] = []
        while True:
            match = _TAG_RE.search(self._buf)
            if not match:
                break
            pre = self._buf[: match.start()].strip()
            if pre:
                output.append(EmotionSegment(emotion=self._current_emotion, text=pre))
            raw_emotion = match.group(1) or match.group(2)
            self._current_emotion = _normalize_emotion(raw_emotion)
            self._buf = self._buf[match.end():]

        # ── Sentence-boundary flush (no-tag fallback) ────────────────
        # When no emotion tags are found and the buffer is long enough,
        # split at the *last* sentence boundary so TTS starts speaking
        # incrementally instead of waiting for the entire LLM response.
        if not output and len(self._buf) >= _SENTENCE_FLUSH_THRESHOLD:
            # Find the last sentence ending (. ! ?) followed by whitespace
            last_match = None
            for m in _SENTENCE_END_RE.finditer(self._buf):
                last_match = m
            if last_match is not None:
                split_pos = last_match.start() + 1  # include the punctuation
                chunk = self._buf[:split_pos].strip()
                self._buf = self._buf[last_match.end():]  # skip past the whitespace
                if chunk:
                    output.append(EmotionSegment(emotion=self._current_emotion, text=chunk))

        return output
