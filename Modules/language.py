"""Per-user language settings for Coffeecord bot messages."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import (
    SUPPORTED_LOCALES,
    invalidate_user_language_cache,
    normalize_language_code,
    resolve_user_language,
    set_user_language,
    t,
    t_for_locale,
)

__module_display_name__ = "Language"
__module_description__ = "Choose your language for Coffeecord replies (English, Spanish, Portuguese, Russian)."
__module_category__ = "configuration"

_LANGUAGE_LABEL_KEYS = {
    "en": "language.choice_en",
    "es": "language.choice_es",
    "pt": "language.choice_pt",
    "ru": "language.choice_ru",
}


def _language_label(code: str) -> str:
    return t_for_locale(code, _LANGUAGE_LABEL_KEYS.get(code, "language.choice_en"))


class LanguageCog(
    commands.GroupCog,
    group_name="language",
    group_description="Choose your language for Coffeecord replies.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="status",
        description="Show your current Coffeecord language.",
    )
    async def language_status(self, interaction: discord.Interaction) -> None:
        code = await resolve_user_language(interaction.user.id)
        embed = discord.Embed(
            title=await t(interaction.user.id, "language.status_title"),
            description=await t(
                interaction.user.id,
                "language.status_body",
                language=_language_label(code),
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="set",
        description="Set your language for Coffeecord replies.",
    )
    @app_commands.describe(
        language="Language for bot replies and embeds you see.",
    )
    @app_commands.choices(
        language=[
            app_commands.Choice(name="English", value="en"),
            app_commands.Choice(name="Español", value="es"),
            app_commands.Choice(name="Português", value="pt"),
            app_commands.Choice(name="Русский", value="ru"),
        ]
    )
    async def language_set(
        self,
        interaction: discord.Interaction,
        language: app_commands.Choice[str],
    ) -> None:
        code = normalize_language_code(language.value)
        if code not in SUPPORTED_LOCALES:
            await interaction.response.send_message(
                await t(interaction.user.id, "common.error_generic"),
                ephemeral=True,
            )
            return
        await set_user_language(interaction.user.id, code)
        invalidate_user_language_cache(interaction.user.id)
        await interaction.response.send_message(
            await t(
                interaction.user.id,
                "language.set_success",
                language=_language_label(code),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LanguageCog(bot))
