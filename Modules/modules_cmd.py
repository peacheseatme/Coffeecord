from collections import defaultdict
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from .module_registry import (
    get_guild_module_states,
    get_registry_map,
    load_module_registry,
    set_module_enabled,
)

MODULES_PER_PAGE = 8


def _chunk[T](items: list[T], size: int) -> list[list[T]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


class ModulesCommandCog(commands.GroupCog, group_name="modules", group_description="View and toggle server modules."):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _module_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        query = (current or "").strip().lower()
        modules = await load_module_registry()
        choices: list[app_commands.Choice[str]] = []
        for module in modules:
            module_id = str(module.get("id", ""))
            display = str(module.get("display_name", module_id))
            if module_id == "modules_cmd":
                continue
            search = f"{module_id} {display}".lower()
            if query and query not in search:
                continue
            choices.append(app_commands.Choice(name=display[:100], value=module_id))
            if len(choices) >= 25:
                break
        return choices

    async def _build_pages(self, guild_id: int) -> list[discord.Embed]:
        modules = await load_module_registry()
        states = await get_guild_module_states(guild_id)

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for module in modules:
            grouped[str(module.get("category", "utilities"))].append(module)

        ordered: list[dict[str, Any]] = []
        for category in sorted(grouped.keys()):
            for module in sorted(grouped[category], key=lambda m: str(m.get("display_name", m.get("id", ""))).lower()):
                ordered.append(module)

        pages: list[discord.Embed] = []
        chunks = _chunk(ordered, MODULES_PER_PAGE) or [[]]
        for idx, chunk in enumerate(chunks, start=1):
            embed = discord.Embed(
                title="Module Status",
                color=discord.Color.blurple(),
                description="Per-server module toggle state.",
            )
            for module in chunk:
                module_id = str(module.get("id", ""))
                enabled = bool(states.get(module_id, bool(module.get("default_enabled", True))))
                icon = "✅" if enabled else "❌"
                embed.add_field(
                    name=f"{icon} {module.get('display_name', module_id)}",
                    value=str(module.get("description", "No description."))[:1024],
                    inline=False,
                )
            embed.set_footer(text=f"Page {idx}/{len(chunks)} • Use /modules toggle <module> to change")
            pages.append(embed)
        return pages

    @app_commands.command(name="status", description="Show all modules and their current state.")
    async def status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        pages = await self._build_pages(interaction.guild.id)
        await interaction.response.send_message(embed=pages[0], ephemeral=True)
        # Keep initial implementation simple and stable (single-page response if multiple pages exist).
        if len(pages) > 1:
            await interaction.followup.send(
                f"There are {len(pages)} pages total. Use `/modules status` after toggles to refresh.",
                ephemeral=True,
            )

    @app_commands.command(name="toggle", description="Toggle a module on or off for this server.")
    @app_commands.autocomplete(module=_module_autocomplete)
    async def toggle(self, interaction: discord.Interaction, module: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        module_id = module.strip().lower()
        if module_id == "modules_cmd":
            await interaction.response.send_message("`modules_cmd` cannot be disabled.", ephemeral=True)
            return
        states = await get_guild_module_states(interaction.guild.id)
        current = bool(states.get(module_id, True))
        new_state = not current
        await set_module_enabled(interaction.guild.id, module_id, new_state)
        await interaction.response.send_message(
            f"Module `{module_id}` is now **{'enabled' if new_state else 'disabled'}**.",
            ephemeral=True,
        )

    @app_commands.command(name="enable", description="Enable a module for this server.")
    @app_commands.autocomplete(module=_module_autocomplete)
    async def enable(self, interaction: discord.Interaction, module: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        module_id = module.strip().lower()
        if module_id == "modules_cmd":
            await interaction.response.send_message("`modules_cmd` is always enabled.", ephemeral=True)
            return
        await set_module_enabled(interaction.guild.id, module_id, True)
        await interaction.response.send_message(f"Module `{module_id}` enabled.", ephemeral=True)

    @app_commands.command(name="disable", description="Disable a module for this server.")
    @app_commands.autocomplete(module=_module_autocomplete)
    async def disable(self, interaction: discord.Interaction, module: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        module_id = module.strip().lower()
        if module_id == "modules_cmd":
            await interaction.response.send_message("`modules_cmd` cannot be disabled.", ephemeral=True)
            return
        await set_module_enabled(interaction.guild.id, module_id, False)
        await interaction.response.send_message(f"Module `{module_id}` disabled.", ephemeral=True)

    @app_commands.command(name="info", description="Show details for a single module.")
    @app_commands.autocomplete(module=_module_autocomplete)
    async def info(self, interaction: discord.Interaction, module: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        module_id = module.strip().lower()
        module_map = await get_registry_map()
        data = module_map.get(module_id)
        if data is None:
            await interaction.response.send_message("Unknown module.", ephemeral=True)
            return
        states = await get_guild_module_states(interaction.guild.id)
        current = bool(states.get(module_id, bool(data.get("default_enabled", True))))
        embed = discord.Embed(
            title=f"Module: {data.get('display_name', module_id)}",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="ID", value=f"`{module_id}`", inline=True)
        embed.add_field(name="Enabled Here", value="Yes" if current else "No", inline=True)
        embed.add_field(name="Default", value="Yes" if bool(data.get("default_enabled", True)) else "No", inline=True)
        embed.add_field(name="Category", value=str(data.get("category", "utilities")), inline=True)
        embed.add_field(name="Path", value=f"`{data.get('path', 'n/a')}`", inline=False)
        embed.add_field(name="Description", value=str(data.get("description", "No description.")), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModulesCommandCog(bot))
