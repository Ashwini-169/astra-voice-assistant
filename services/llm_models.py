"""Shared models for the LLM service stack."""
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

ProviderName = Literal["ollama", "lmstudio", "openai", "custom"]


class RuntimeSettings(BaseModel):
    provider: ProviderName = "ollama"
    model: str
    temperature: float = 0.7
    max_tokens: int = 300
    top_p: float = 0.95
    stop: List[str] = Field(default_factory=list)
    stream: bool = False
    voice_mode: bool = False
    ollama_url: str
    lmstudio_url: str = "http://127.0.0.1:1234"
    openai_url: str = "https://api.openai.com/v1"
    openai_api_key: str = ""
    custom_url: str = ""
    custom_api_key: str = ""
    custom_mode: Literal["openai", "prompt"] = "openai"


class SettingsUpdate(BaseModel):
    provider: Optional[ProviderName] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    stream: Optional[bool] = None
    voice_mode: Optional[bool] = None
    ollama_url: Optional[str] = None
    lmstudio_url: Optional[str] = None
    openai_url: Optional[str] = None
    openai_api_key: Optional[str] = None
    custom_url: Optional[str] = None
    custom_api_key: Optional[str] = None
    custom_mode: Optional[Literal["openai", "prompt"]] = None


class GenerateRequest(BaseModel):
    prompt: str
    provider: Optional[ProviderName] = None
    model: Optional[str] = None
    stream: Optional[bool] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[List[str]] = None
    voice_mode: Optional[bool] = None


class GenerateResponse(BaseModel):
    provider: str
    model: str
    response: str
    request_id: str


class LLMRequest(BaseModel):
    provider: ProviderName
    model: str
    prompt: str
    temperature: float
    max_tokens: int
    top_p: float
    stop: List[str] = Field(default_factory=list)
    stream: bool = False
    voice_mode: bool = False


class MCPServerConfig(BaseModel):
    name: str
    base_url: str
    description: str = ""
    enabled: bool = True
    tools: List[str] = Field(default_factory=list)
    auth_header: Optional[str] = None


class MCPToolCallRequest(BaseModel):
    server: str
    tool: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class BrowserSearchRequest(BaseModel):
    query: str
    limit: int = 5


class FileSearchRequest(BaseModel):
    query: str
    limit: int = 20
    path: str = "."


class MusicControlRequest(BaseModel):
    action: Literal["play", "pause", "resume", "stop", "next", "previous", "set_volume"]
    value: Optional[int] = None


class AgentLoopRequest(BaseModel):
    prompt: str
    max_steps: int = 3
    provider: Optional[ProviderName] = None
    model: Optional[str] = None
    temperature: Optional[float] = None

