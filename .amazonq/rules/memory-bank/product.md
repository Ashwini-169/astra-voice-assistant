# Voice2 - Product Overview

## Purpose
Voice2 is a local-first, low-latency voice assistant for Windows laptops. It runs entirely on-device without cloud dependency, targeting consumer hardware with sufficient GPU/CPU throughput.

## Value Proposition
- Fastest local voice assistant for Windows
- No cloud dependency — all inference runs on-device
- Interruption-safe duplex conversation
- Sub-second first-audio latency via adaptive chunking

## Key Features

### Speech Recognition (ASR)
- Faster-Whisper (CTranslate2) for low-latency transcription
- DirectML acceleration on Windows (onnxruntime-directml)
- Local model cache via HF_HOME=.hf_cache

### LLM Inference
- Multi-provider gateway: Ollama (default), LM Studio, OpenAI, custom OpenAI-compatible
- Default model: qwen2.5:3b via Ollama
- Streaming token output with cancellation support
- GPU serialization via gpu_lock to prevent contention

### Text-to-Speech (TTS)
- Three backends: Edge TTS (default), Piper, Fish-Speech (OpenAudio S1 Mini)
- Edge TTS: Microsoft Neural voices (en-IN-NeerjaNeural default), emotion-aware voice/rate/pitch
- Piper: offline fallback, strips emotion tags, sends plain text
- Fish-Speech: passes emotion tags natively for expressive synthesis
- Automatic offline detection with configurable TTL cache
- Runtime settings API for live tuning without restart

### Emotion & Humanization
- Emotion tagging in LLM output (e.g. `(excited)text`)
- Prosody engine applies speech rate/pitch adjustments per emotion
- Speech normalizer converts markdown to speakable text
- Voice style profiles (e.g. INDIAN_NEUTRAL_FEMALE)

### Memory
- Semantic memory via Qdrant vector store (local)
- Sentence-transformer embeddings (all-MiniLM-L6-v2)
- Retrieval-augmented prompts with past conversation context

### Duplex / Interruption
- VAD-based speech capture
- Interrupt controller with cancellation events
- State machine: IDLE → LISTENING → THINKING → SPEAKING → INTERRUPTED
- Stale-generation guards prevent old tokens reaching TTS after interruption

### Intent Routing
- ONNX-based intent classifier (port 8004)
- Routes non-chat intents away from LLM pipeline
- Agent control plane for multi-step task execution

## Services Architecture (Ports)
| Service | Port | Description |
|---------|------|-------------|
| Whisper ASR | 8001 | Speech-to-text |
| LLM | 8002 | Language model inference |
| TTS | 8003 | Speech synthesis + playback |
| Intent | 8004 | Intent classification |

## Target Users
- Developers building local AI assistants
- Privacy-conscious users wanting on-device voice AI
- Windows laptop users with modern GPU/CPU

## Frontend
React/TypeScript SPA (Vite) providing a chat UI with real-time audio synthesis via `/synthesize` endpoint.
