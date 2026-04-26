from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class ChatResponse:
    provider: str
    message: str
    conversation_id: Optional[str] = None
    model: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ProviderStatus:
    provider: str
    active: bool
    session_live: bool
    error: Optional[str] = None
    last_checked: float = field(default_factory=time.time)


class BaseProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def login(self) -> bool:
        """Authenticate and establish a browser session."""
        ...

    @abstractmethod
    async def send_message(self, message: str, conversation_id: Optional[str] = None) -> ChatResponse:
        """Send a message and return the response."""
        ...

    @abstractmethod
    async def get_response(self) -> str:
        """Wait for and extract the LLM's response from the page."""
        ...

    @abstractmethod
    async def check_session(self) -> bool:
        """Return True if the current session is still valid."""
        ...

    @abstractmethod
    async def reset_session(self) -> bool:
        """Clear and re-establish the session."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Clean up browser resources."""
        ...
