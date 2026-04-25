from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import AnyHttpUrl, Field


class Settings(BaseSettings):
    whisper_host: str = Field(default="0.0.0.0")
    whisper_port: int = Field(default=8001)

    llm_host: str = Field(default="0.0.0.0")
    llm_port: int = Field(default=8002)
    ollama_api_url: AnyHttpUrl = Field(default="http://127.0.0.1:11434")
    llm_model: str = Field(default="qwen2.5:3b")
    llm_provider: str = Field(default="ollama")
    lmstudio_api_url: AnyHttpUrl = Field(default="http://127.0.0.1:1234")
    openai_api_url: AnyHttpUrl = Field(default="https://api.openai.com/v1")
    openai_api_key: str = Field(default="")
    custom_llm_api_url: str = Field(default="")
    custom_llm_api_key: str = Field(default="")
    custom_llm_mode: str = Field(default="openai")

    tts_host: str = Field(default="0.0.0.0")
    tts_port: int = Field(default=8003)
    piper_api_url: AnyHttpUrl = Field(default="http://127.0.0.1:59125")
    piper_voice: str = Field(default="en_US-lessac-medium")
    piper_speaker_id: int | None = Field(default=None)

    intent_host: str = Field(default="0.0.0.0")
    intent_port: int = Field(default=8004)
    intent_model_path: str = Field(default="models/intent.onnx")

    log_level: str = Field(default="INFO")

    # LLM generation parameters — controls response length and context window
    llm_num_predict: int = Field(default=300, description="Max tokens per LLM response (0=unlimited)")
    llm_num_ctx: int = Field(default=2048, description="Context window size for LLM")
    llm_temperature: float = Field(default=0.7, description="Sampling temperature for text generation")
    llm_top_p: float = Field(default=0.95, description="Nucleus sampling value")

    # TTS backend: "edge" uses Microsoft Edge TTS (default, no server needed)
    # "piper" strips emotion tags and sends to Piper server
    # "fish_speech" passes emotion tags natively to OpenAudio S1 Mini
    tts_backend: str = Field(default="edge")
    fish_speech_api_url: AnyHttpUrl = Field(default="http://127.0.0.1:8080")
    tts_edge_offline_fallback_enabled: bool = Field(default=True)
    tts_edge_offline_check_url: str = Field(default="https://www.microsoft.com")
    tts_edge_offline_check_timeout_sec: float = Field(default=0.5)
    tts_edge_offline_state_ttl_sec: float = Field(default=3.0)
    tts_edge_timeout_sec: float = Field(default=1.5)

    class Config:
        env_prefix = "AI_ASSISTANT_"
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
