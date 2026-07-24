"""Shared helpers for moderation / module log actor labels."""

from __future__ import annotations

from typing import Union

import discord
from discord.ext import commands

SYSTEM_LOG_ACTOR_LABEL = "SYSTEM"

LogActor = Union[discord.abc.User, "_SystemLogActor", None]


class _SystemLogActor:
    """Sentinel: action originated from the host console (`c-cord console`)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "SYSTEM_LOG_ACTOR"


SYSTEM_LOG_ACTOR: LogActor = _SystemLogActor()


def format_log_actor(actor: LogActor) -> str:
    if actor is SYSTEM_LOG_ACTOR:
        return SYSTEM_LOG_ACTOR_LABEL
    if actor is None:
        return "Unknown"
    return f"{actor.mention} (`{actor.id}`)"


def is_host_console_interaction(interaction: discord.Interaction) -> bool:
    return bool(getattr(interaction, "is_host_console", False))


def log_actor_from_interaction(interaction: discord.Interaction) -> LogActor:
    if is_host_console_interaction(interaction):
        return SYSTEM_LOG_ACTOR
    return interaction.user


def log_actor_from_context(ctx: commands.Context) -> LogActor:
    if bool(getattr(ctx, "is_host_console", False)):
        return SYSTEM_LOG_ACTOR
    return ctx.author
