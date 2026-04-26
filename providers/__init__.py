from .base import BaseProvider, ChatResponse, ProviderStatus
from .claude import ClaudeProvider
from .chatgpt import ChatGPTProvider
from .gemini import GeminiProvider
from .grok import GrokProvider
from .perplexity import PerplexityProvider
from .copilot import CopilotProvider

PROVIDER_MAP = {
    "claude": ClaudeProvider,
    "chatgpt": ChatGPTProvider,
    "gemini": GeminiProvider,
    "grok": GrokProvider,
    "perplexity": PerplexityProvider,
    "copilot": CopilotProvider,
}

__all__ = [
    "BaseProvider",
    "ChatResponse",
    "ProviderStatus",
    "ClaudeProvider",
    "ChatGPTProvider",
    "GeminiProvider",
    "GrokProvider",
    "PerplexityProvider",
    "CopilotProvider",
    "PROVIDER_MAP",
]
