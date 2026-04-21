"""Channel ABC + DeliveryReceipt."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class DeliveryReceipt:
    channel: str
    status: str       # 'ok' | 'failed'
    error: str | None = None


class Channel(ABC):
    """A destination (Telegram, Webhook, Email). Concrete impls implement send."""

    name: str = "base"

    @abstractmethod
    def send(self, message: str, **kwargs) -> DeliveryReceipt:
        raise NotImplementedError
