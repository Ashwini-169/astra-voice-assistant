"""Provider registry."""
from services.providers import custom, lmstudio, ollama, openai

PROVIDER_MODULES = {
    "ollama": ollama,
    "lmstudio": lmstudio,
    "openai": openai,
    "custom": custom,
}

