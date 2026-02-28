"""Speech normalizer — strips markdown / formatting for clean TTS input.

The LLM often outputs markdown (bold, headings, bullets, code blocks,
tables, emojis) that sound terrible when spoken verbatim by TTS.

This module provides ``markdown_to_speech()`` which converts markdown-
flavoured text into a plain, speakable version.  The original LLM output
is preserved elsewhere for display.

Usage in the TTS pipeline::

    from humanization.speech_normalizer import markdown_to_speech

    speech_text = markdown_to_speech(raw_llm_segment)
    # send speech_text to TTS
"""

import re
from typing import Optional

__all__ = ["markdown_to_speech"]


# ── Pre-compiled patterns (order matters) ────────────────────────────────────

# Fenced code blocks  ```lang ... ```
_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n.*?```", re.DOTALL)

# Inline code  `...`
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")

# Bold + italic  ***text***  or ___text___
_BOLD_ITALIC_RE = re.compile(r"\*{3}(.+?)\*{3}|_{3}(.+?)_{3}")

# Bold  **text**  or __text__
_BOLD_RE = re.compile(r"\*{2}(.+?)\*{2}|_{2}(.+?)_{2}")

# Italic  *text*  or _text_  (single, word-boundary aware)
_ITALIC_RE = re.compile(r"(?<!\w)\*(.+?)\*(?!\w)|(?<!\w)_(.+?)_(?!\w)")

# Strikethrough  ~~text~~
_STRIKE_RE = re.compile(r"~~(.+?)~~")

# Markdown headings  # ... ######
_HEADING_RE = re.compile(r"^#{1,6}\s*", re.MULTILINE)

# Bullet / numbered list prefixes
_LIST_RE = re.compile(r"^\s*(?:[-•*+]|\d+[.)]\s)", re.MULTILINE)

# Table separator rows  |---|---|
_TABLE_SEP_RE = re.compile(r"^\s*\|[\s:|-]+\|\s*$", re.MULTILINE)

# Table cell pipes
_TABLE_PIPE_RE = re.compile(r"\|")

# Markdown links  [text](url)
_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")

# Markdown images  ![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]+\)")

# Horizontal rules  --- / *** / ___
_HR_RE = re.compile(r"^[\s]*[-*_]{3,}\s*$", re.MULTILINE)

# HTML tags  <br>, <p>, etc.
_HTML_RE = re.compile(r"<[^>]+>")

# Blockquotes  > text
_BLOCKQUOTE_RE = re.compile(r"^\s*>+\s?", re.MULTILINE)

# Stray asterisks / underscores that survived (catch-all)
_STRAY_STAR_RE = re.compile(r"(?<!\w)[*_]+(?!\w)")

# Multiple whitespace on one line
_MULTI_SPACE_RE = re.compile(r"[ \t]{2,}")

# 3+ newlines → 2
_MULTI_NL_RE = re.compile(r"\n{3,}")

# Emoji ranges  (broad Unicode blocks)
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001F9FF"   # Misc Symbols, Emoticons, etc.
    "\U00002702-\U000027B0"   # Dingbats
    "\U0000FE00-\U0000FE0F"   # Variation Selectors
    "\U0000200D"               # ZWJ
    "\U000025A0-\U000025FF"   # Geometric Shapes
    "]+",
    flags=re.UNICODE,
)


def markdown_to_speech(text: str) -> str:
    """Convert markdown text to a clean, speakable version.

    The function is idempotent — running it twice on the same input
    produces the same result.

    Parameters
    ----------
    text : str
        Raw LLM output (may contain markdown formatting).

    Returns
    -------
    str
        Plain text suitable for TTS.
    """
    if not text:
        return text

    # ── 1. Block-level removal (code blocks, HRs, table-sep rows) ────
    out = _CODE_BLOCK_RE.sub("", text)
    out = _HR_RE.sub("", out)
    out = _TABLE_SEP_RE.sub("", out)

    # ── 2. Images before links (images have leading !) ────────────────
    out = _IMAGE_RE.sub(r"\1", out)   # keep alt text
    out = _LINK_RE.sub(r"\1", out)    # keep link text

    # ── 3. Inline formatting → plain text ─────────────────────────────
    out = _BOLD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), out)
    out = _BOLD_RE.sub(lambda m: m.group(1) or m.group(2), out)
    out = _ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), out)
    out = _STRIKE_RE.sub(r"\1", out)
    out = _INLINE_CODE_RE.sub(r"\1", out)

    # ── 4. Line-level cleanup ─────────────────────────────────────────
    out = _HEADING_RE.sub("", out)
    out = _BLOCKQUOTE_RE.sub("", out)
    out = _LIST_RE.sub("", out)
    out = _TABLE_PIPE_RE.sub(" ", out)
    out = _HTML_RE.sub("", out)

    # ── 5. Stray formatting chars + emojis ────────────────────────────
    out = _STRAY_STAR_RE.sub("", out)
    out = _EMOJI_RE.sub("", out)

    # ── 6. Whitespace normalisation ───────────────────────────────────
    out = _MULTI_SPACE_RE.sub(" ", out)
    out = _MULTI_NL_RE.sub("\n\n", out)

    # Strip each line and drop empty lines
    lines = [line.strip() for line in out.splitlines()]
    out = "\n".join(line for line in lines if line)

    return out.strip()
