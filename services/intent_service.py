"""Intent classification service targeting NPU (DirectML) with CPU fallback."""
import logging
import os
from typing import Dict, List, Optional

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from core.config import get_settings

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Intent Service", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_session = None
_providers: List[str] = []
_fallback_mode = False


class IntentRequest(BaseModel):
    text: str


class IntentResponse(BaseModel):
    label: str
    scores: Dict[str, float]
    provider: Optional[str]


def _load_providers():
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - install-time concern
        raise RuntimeError("onnxruntime not installed") from exc

    available = list(ort.get_available_providers())
    if any(provider.lower().startswith("dml") for provider in available):
        return ["DmlExecutionProvider", "CPUExecutionProvider"], available
    return ["CPUExecutionProvider"], available


def load_intent_model():
    global _session, _providers, _fallback_mode
    if _session is not None:
        return _session

    settings = get_settings()
    providers, discovered = _load_providers()

    if not os.path.exists(settings.intent_model_path):
        logger.warning("Intent model not found at %s; enabling fallback mode", settings.intent_model_path)
        _fallback_mode = True
        _providers = discovered
        return None

    try:
        import onnxruntime as ort

        _session = ort.InferenceSession(
            settings.intent_model_path,
            providers=providers,
        )
        _providers = discovered
        logger.info(
            "Loaded intent model from %s using providers %s (preferred=%s)",
            settings.intent_model_path,
            discovered,
            providers,
        )
        return _session
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Failed to load intent model; enabling fallback mode: %s", exc)
        _fallback_mode = True
        _session = None
        _providers = discovered
        return None


@app.on_event("startup")
async def startup_event() -> None:
    load_intent_model()


def _fallback_intent(text: str) -> str:
    lowered = text.lower().strip()
    if any(token in lowered for token in ("bye", "goodbye", "exit", "quit")):
        return "goodbye"
    return "chat"


def _text_to_features(text: str, max_length: int = 64) -> np.ndarray:
    encoded = [ord(ch) % 256 for ch in text][:max_length]
    padded = encoded + [0] * (max_length - len(encoded))
    return np.array([padded], dtype=np.float32)


def _softmax(logits: np.ndarray) -> np.ndarray:
    shifted = logits - np.max(logits)
    exps = np.exp(shifted)
    return exps / np.sum(exps)


@app.post("/classify", response_model=IntentResponse)
async def classify(request: IntentRequest) -> IntentResponse:
    if _fallback_mode or _session is None:
        label = _fallback_intent(request.text)
        return IntentResponse(label=label, scores={"chat": 1.0 if label == "chat" else 0.0}, provider="fallback")

    features = _text_to_features(request.text)
    try:
        inputs = {_session.get_inputs()[0].name: features}
        outputs = _session.run(None, inputs)
        logits = outputs[0].squeeze()
        probabilities = _softmax(logits)
        scores = {f"label_{idx}": float(score) for idx, score in enumerate(probabilities)}
        best_index = int(np.argmax(probabilities))
        label = "chat" if best_index == 0 else f"label_{best_index}"
        provider = _session.get_providers()[0] if _session.get_providers() else None
        return IntentResponse(label=label, scores=scores, provider=provider)
    except Exception as exc:  # pylint: disable=broad-except
        logger.error("Intent classification failed: %s", exc)
        raise HTTPException(status_code=500, detail="Intent classification failed") from exc


@app.get("/health")
async def health():
    return {"status": "ok", "service": "intent", "fallback_mode": _fallback_mode}


if __name__ == "__main__":
    from uvicorn import run

    settings = get_settings()
    run(app, host=settings.intent_host, port=settings.intent_port, log_level=settings.log_level.lower())
