"""Relay adapters — one per fan-out target."""

from . import discord_adapter
from . import slack_adapter
from . import log_adapter
from . import sse_adapter
from . import device_adapter

__all__ = [
    "discord_adapter",
    "slack_adapter",
    "log_adapter",
    "sse_adapter",
    "device_adapter",
]
