"""CLI entrypoint for orchestrator pipeline."""
import argparse
import asyncio
import json
import logging
import time
from typing import Any

import httpx

from duplex.audio_listener import AudioListener
from duplex.interrupt_controller import InterruptController
from duplex.speech_capture import CaptureDiagnostics, SpeechCapture
from duplex.state_machine import AssistantStateController
from duplex.vad_engine import VADEngine
from core.config import get_settings
from orchestrator.memory_buffer import ConversationBuffer
from orchestrator.pipeline import run_pipeline, run_pipeline_streaming

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI Assistant Orchestrator")
    parser.add_argument("--text", help="User input text")
    parser.add_argument("--stream", action="store_true", help="Use streaming LLM/TTS pipeline")
    parser.add_argument("--semi-duplex", action="store_true", help="Enable safe semi-duplex interrupt handling")
    parser.add_argument("--duplex", action="store_true", help="Run continuous mic -> whisper -> response loop")
    parser.add_argument("--whisper-test", action="store_true", help="Capture mic utterance and transcribe via Whisper only")
    parser.add_argument("--no-visual", action="store_true", help="Disable state visual feedback logs")
    return parser.parse_args()


def print_result(result: Any) -> None:
    print(json.dumps(result, indent=2, default=str))


async def _check_service(url: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(url)
            return response.status_code == 200
    except Exception:  # pylint: disable=broad-except
        return False


def _preflight_for_voice(max_wait_seconds: int = 90) -> None:
    settings = get_settings()
    whisper_url = f"http://127.0.0.1:{settings.whisper_port}/health"
    llm_url = f"http://127.0.0.1:{settings.llm_port}/health"
    tts_url = f"http://127.0.0.1:{settings.tts_port}/health"

    deadline = time.perf_counter() + max_wait_seconds
    missing = ["whisper", "llm", "tts"]
    while time.perf_counter() < deadline:
        checks = {
            "whisper": asyncio.run(_check_service(whisper_url)),
            "llm": asyncio.run(_check_service(llm_url)),
            "tts": asyncio.run(_check_service(tts_url)),
        }
        missing = [name for name, ok in checks.items() if not ok]
        if not missing:
            return
        logger.info("[voice] Waiting for services: %s", ", ".join(missing))
        time.sleep(2)

    raise RuntimeError(
        f"Services not reachable after {max_wait_seconds}s: {', '.join(missing)}. Start stack first with .\\start_stack.ps1 -ServicesOnly"
    )


def _preflight_for_whisper(max_wait_seconds: int = 90) -> None:
    settings = get_settings()
    whisper_url = f"http://127.0.0.1:{settings.whisper_port}/health"
    deadline = time.perf_counter() + max_wait_seconds
    while time.perf_counter() < deadline:
        if asyncio.run(_check_service(whisper_url)):
            return
        logger.info("[voice] Waiting for service: whisper")
        time.sleep(2)
    raise RuntimeError(
        f"Whisper service is not reachable after {max_wait_seconds}s. Start it with .\\start_stack.ps1 -ServicesOnly"
    )


async def _transcribe_wav_bytes(wav_bytes: bytes) -> str:
    settings = get_settings()
    base_url = f"{settings.whisper_host}:{settings.whisper_port}" if settings.whisper_host.startswith("http") else f"http://127.0.0.1:{settings.whisper_port}"
    last_exc: Exception | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for attempt in range(1, 4):
            try:
                files = {"audio_file": ("live.wav", wav_bytes, "audio/wav")}
                response = await client.post(f"{base_url}/transcribe", files=files)
                response.raise_for_status()
                data = response.json()
                return str(data.get("text", "")).strip()
            except (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError, httpx.ConnectError) as exc:
                last_exc = exc
                logger.warning("[voice] Whisper transcribe retry %s/3 due to %s", attempt, type(exc).__name__)
                await asyncio.sleep(1.0)
            except Exception as exc:  # pylint: disable=broad-except
                last_exc = exc
                break

    if last_exc is not None:
        raise RuntimeError(f"Whisper transcription failed: {type(last_exc).__name__}: {last_exc}") from last_exc
    raise RuntimeError("Whisper transcription failed")


def _capture_user_text(capture: SpeechCapture) -> tuple[str, float]:
    """Capture speech and transcribe. Returns (text, whisper_ms)."""
    if not capture.available:
        raise RuntimeError("sounddevice not installed; install it to use voice mode")

    last_diag = CaptureDiagnostics()
    last_transcribe_error = ""

    for attempt in range(1, 4):
        logger.info("[voice] Listening for one utterance (attempt %s/3)...", attempt)
        wav_bytes, diag = capture.capture_utterance_wav_with_diagnostics(wait_seconds=40.0)
        last_diag = diag
        if wav_bytes is None:
            logger.info(
                "[voice] No speech detected on attempt %s (frames=%s, avg_rms=%.1f, max_rms=%.1f).",
                attempt,
                diag.total_frames,
                diag.avg_rms,
                diag.max_rms,
            )
            continue

        whisper_start = time.perf_counter()
        try:
            user_text = asyncio.run(_transcribe_wav_bytes(wav_bytes))
        except Exception as exc:  # pylint: disable=broad-except
            last_transcribe_error = str(exc)
            logger.warning("[voice] Transcription failed on attempt %s: %s", attempt, exc)
            continue
        whisper_ms = (time.perf_counter() - whisper_start) * 1000

        if not user_text:
            logger.info("[voice] Empty transcription on attempt %s.", attempt)
            continue

        logger.info("[voice] You said: %s  (whisper: %.0f ms)", user_text, whisper_ms)
        return user_text, whisper_ms

    if last_diag.speech_frames == 0:
        raise RuntimeError(
            "Unable to capture valid speech after 3 attempts. "
            f"VAD saw no speech (avg_rms={last_diag.avg_rms:.1f}, max_rms={last_diag.max_rms:.1f}). "
            "Microphone input is likely too low or wrong device is selected."
        )

    raise RuntimeError(
        "Microphone captured speech, but transcription failed after 3 attempts. "
        f"VAD stats: speech_frames={last_diag.speech_frames}, avg_rms={last_diag.avg_rms:.1f}, max_rms={last_diag.max_rms:.1f}. "
        f"Last Whisper error: {last_transcribe_error or 'unknown'}"
    )


def _log_turn_summary(turn: int, whisper_ms: float, result: Any) -> None:
    """Print a compact timing summary after each turn."""
    t = result.timings_ms if hasattr(result, "timings_ms") else {}
    total = whisper_ms + t.get("intent_ms", 0) + t.get("llm_ms", 0) + t.get("tts_ms", 0)
    logger.info(
        "\n╔══════════════ Turn %d Timing ══════════════╗\n"
        "║  Whisper ASR:  %7.0f ms                  ║\n"
        "║  Intent:       %7.0f ms                  ║\n"
        "║  LLM (stream): %7.0f ms  (1st tok %s ms) ║\n"
        "║  TTS:          %7.0f ms                  ║\n"
        "║  Memory+Embed: %7.0f ms                  ║\n"
        "║  ──────────────────────────────────────── ║\n"
        "║  TOTAL:        %7.0f ms                  ║\n"
        "╚═══════════════════════════════════════════╝",
        turn,
        whisper_ms,
        t.get("intent_ms", 0),
        t.get("llm_ms", 0),
        "?",  # first-token logged separately by llm_streamer
        t.get("tts_ms", 0),
        t.get("embedding_ms", 0) + t.get("memory_ms", 0),
        total,
    )


def _run_duplex_loop(
    buffer: ConversationBuffer,
    visual_feedback: bool,
    state_controller: AssistantStateController,
    interrupt_controller: InterruptController,
    audio_listener: AudioListener,
    capture: SpeechCapture,
) -> None:
    if not capture.available:
        raise RuntimeError("sounddevice not installed; install it to use --duplex mode")

    turn = 1
    while True:
        logger.info("[duplex] Turn %s: waiting for speech...", turn)
        wav_bytes = capture.capture_utterance_wav(wait_seconds=30.0)
        if wav_bytes is None:
            continue

        whisper_start = time.perf_counter()
        try:
            user_text = asyncio.run(_transcribe_wav_bytes(wav_bytes))
        except Exception as exc:  # pylint: disable=broad-except
            logger.error("[duplex] Whisper transcribe failed: %s", exc)
            continue
        whisper_ms = (time.perf_counter() - whisper_start) * 1000

        if not user_text:
            logger.info("[duplex] Empty transcription, waiting for next utterance")
            continue

        logger.info("[duplex] You said: %s  (whisper: %.0f ms)", user_text, whisper_ms)

        result = asyncio.run(
            run_pipeline_streaming(
                user_text,
                buffer,
                interrupt_controller=interrupt_controller,
                state_controller=state_controller,
                audio_listener=audio_listener,
                visual_feedback=visual_feedback,
            )
        )
        # Inject whisper timing into result
        if hasattr(result, "timings_ms"):
            result.timings_ms["whisper_ms"] = whisper_ms

        _log_turn_summary(turn, whisper_ms, result)
        print_result(result.dict())
        turn += 1


def main() -> None:
    args = parse_args()
    if not args.duplex and not args.semi_duplex and not args.whisper_test and not args.text:
        raise SystemExit("--text is required unless --duplex is used")

    buffer = ConversationBuffer()
    state_controller = AssistantStateController()
    interrupt_controller = InterruptController()
    visual_feedback = not args.no_visual
    vad = VADEngine(aggressiveness=3)
    capture_vad = VADEngine(aggressiveness=2)
    last_vad_state: bool | None = None
    last_vad_log_ts = 0.0

    def _on_vad(is_speech: bool) -> None:
        nonlocal last_vad_state, last_vad_log_ts
        if visual_feedback:
            now = time.perf_counter()
            if last_vad_state is is_speech and (now - last_vad_log_ts) < 0.8:
                return
            marker = "🎙 Listening..." if is_speech else "🔇 Silence..."
            logger.info(marker)
            last_vad_state = is_speech
            last_vad_log_ts = now

    audio_listener = AudioListener(vad, interrupt_controller, on_vad=_on_vad)
    speech_capture = SpeechCapture(capture_vad)

    try:
        if args.whisper_test:
            _preflight_for_whisper()
            user_text, whisper_ms = _capture_user_text(speech_capture)
            print_result({"transcript": user_text, "whisper_ms": round(whisper_ms, 1)})
            return
        if args.duplex:
            _preflight_for_voice()
            audio_listener.start()
            _run_duplex_loop(buffer, visual_feedback, state_controller, interrupt_controller, audio_listener, speech_capture)
        elif args.semi_duplex and not args.text:
            _preflight_for_voice()
            user_text, whisper_ms = _capture_user_text(speech_capture)
            audio_listener.start()
            result = asyncio.run(
                run_pipeline_streaming(
                    user_text,
                    buffer,
                    interrupt_controller=interrupt_controller,
                    state_controller=state_controller,
                    audio_listener=audio_listener,
                    visual_feedback=visual_feedback,
                )
            )
            if hasattr(result, "timings_ms"):
                result.timings_ms["whisper_ms"] = whisper_ms
            _log_turn_summary(1, whisper_ms, result)
        elif args.stream or args.semi_duplex:
            if args.semi_duplex:
                audio_listener.start()
            result = asyncio.run(
                run_pipeline_streaming(
                    args.text or "",
                    buffer,
                    interrupt_controller=interrupt_controller,
                    state_controller=state_controller,
                    audio_listener=audio_listener if args.semi_duplex else None,
                    visual_feedback=visual_feedback,
                )
            )
        else:
            result = asyncio.run(run_pipeline(args.text or "", buffer))
        print_result(result.dict())
    except KeyboardInterrupt:
        print(json.dumps({"error": "Interrupted by user"}))
    except Exception as exc:  # pylint: disable=broad-except
        error_text = str(exc) or repr(exc)
        logger.exception("orchestrator failed")
        print(json.dumps({"error": error_text}))
    finally:
        audio_listener.stop()


if __name__ == "__main__":
    main()
