from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from . import json_cache
from .module_registry import is_module_enabled

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "Storage" / "Data"
STAFF_HISTORY_PATH = DATA_DIR / "staff_history.json"
LOCKDOWN_STATE_PATH = DATA_DIR / "lockdown_state.json"
WARNS_PATH = DATA_DIR / "warns.json"

LOCKDOWN_EDIT_DELAY_SECONDS = 0.25
ROLE_BULK_EDIT_DELAY_SECONDS = 0.2
PURGE_DELETE_DELAY_SECONDS = 0.12
LOCKDOWN_MESSAGE_COOLDOWN_SECONDS = 12.0
MAX_ROLE_LIST_CHARS = 900
MAX_HISTORY_LINES = 8

INVITE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+",
    re.IGNORECASE,
)

LOCKDOWN_PERMISSIONS: tuple[str, ...] = (
    "view_channel",
    "send_messages",
    "create_public_threads",
    "create_private_threads",
    "send_messages_in_threads",
)

TARGET_TYPE_CHOICES = [
    app_commands.Choice(name="All Members", value="all"),
    app_commands.Choice(name="Humans Only", value="humans"),
    app_commands.Choice(name="Bots Only", value="bots"),
]

__module_display_name__ = "Staff Utilities"
__module_description__ = "Advanced staff moderation utilities with safety flows."
__module_category__ = "moderation"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_ts(ts: float | int | str | None) -> str:
    try:
        if isinstance(ts, str):
            if ts.isdigit():
                ts = int(ts)
            else:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return f"<t:{int(dt.timestamp())}:f>"
        if isinstance(ts, (int, float)):
            return f"<t:{int(ts)}:f>"
    except Exception:
        pass
    return "Unknown time"


def _embed(title: str, description: str, color: discord.Color) -> discord.Embed:
    em = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
    em.set_footer(text="Coffeecord Staff Utilities")
    return em


def _staff_bypass(member: discord.Member) -> bool:
    perms = member.guild_permissions
    return perms.manage_guild or perms.manage_messages or perms.administrator


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    raw = json_cache.get(path, default)
    return raw if isinstance(raw, dict) else dict(default)


def _save_json(path: Path, data: dict[str, Any]) -> None:
    json_cache.set_(path, data)


def _get_history_data() -> dict[str, Any]:
    return _load_json(STAFF_HISTORY_PATH, {"guilds": {}})


def _save_history_data(data: dict[str, Any]) -> None:
    _save_json(STAFF_HISTORY_PATH, data)


def _get_lockdown_data() -> dict[str, Any]:
    return _load_json(LOCKDOWN_STATE_PATH, {"guilds": {}})


def _save_lockdown_data(data: dict[str, Any]) -> None:
    _save_json(LOCKDOWN_STATE_PATH, data)


def _guild_history_bucket(history: dict[str, Any], guild_id: int) -> dict[str, Any]:
    guilds = history.setdefault("guilds", {})
    return guilds.setdefault(str(guild_id), {"users": {}})


def _user_history_row(history: dict[str, Any], guild_id: int, user_id: int) -> dict[str, Any]:
    bucket = _guild_history_bucket(history, guild_id)
    users = bucket.setdefault("users", {})
    return users.setdefault(str(user_id), {"notes": []})


def _lockdown_guild_row(lockdown: dict[str, Any], guild_id: int) -> Optional[dict[str, Any]]:
    return lockdown.get("guilds", {}).get(str(guild_id))


def _warn_entries(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    raw = _load_json(WARNS_PATH, {})
    guild_raw = raw.get(str(guild_id), {})
    if not isinstance(guild_raw, dict):
        return []
    entries = guild_raw.get(str(user_id), [])
    if not isinstance(entries, list):
        return []
    out: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _shorten(text: str, max_len: int = 140) -> str:
    value = text.strip()
    if len(value) <= max_len:
        return value
    return value[: max_len - 1].rstrip() + "…"


def _snapshot_lockdown_overwrites(overwrite: discord.PermissionOverwrite) -> dict[str, Optional[bool]]:
    return {perm: getattr(overwrite, perm, None) for perm in LOCKDOWN_PERMISSIONS}


def _locked_roles_for_cutoff(guild: discord.Guild, cutoff: discord.Role) -> list[discord.Role]:
    """Roles at or below the cutoff (inclusive) receive lockdown denies."""
    pos = cutoff.position
    return [r for r in guild.roles if r.position <= pos]


def _exempt_roles_for_cutoff(guild: discord.Guild, cutoff: discord.Role) -> list[discord.Role]:
    """Roles above the cutoff receive explicit allows so staff keeps access when lower roles are denied."""
    pos = cutoff.position
    return [r for r in guild.roles if r.position > pos and not r.is_default()]


def _apply_lockdown_denies_to_overwrite(
    overwrite: discord.PermissionOverwrite,
    *,
    hide_channels: bool,
    disable_messages: bool,
    disable_threads: bool,
) -> bool:
    changed = False
    if hide_channels and getattr(overwrite, "view_channel", None) is not False:
        overwrite.view_channel = False
        changed = True
    if disable_messages and hasattr(overwrite, "send_messages") and getattr(overwrite, "send_messages") is not False:
        overwrite.send_messages = False
        changed = True
    if disable_threads:
        for perm_name in ("create_public_threads", "create_private_threads", "send_messages_in_threads"):
            if hasattr(overwrite, perm_name) and getattr(overwrite, perm_name) is not False:
                setattr(overwrite, perm_name, False)
                changed = True
    return changed


def _apply_lockdown_allows_to_overwrite(
    overwrite: discord.PermissionOverwrite,
    *,
    hide_channels: bool,
    disable_messages: bool,
    disable_threads: bool,
) -> bool:
    """Explicit allows for staff roles above the cutoff (undo @everyone-style lock for them)."""
    changed = False
    if hide_channels and getattr(overwrite, "view_channel", None) is not True:
        overwrite.view_channel = True
        changed = True
    if disable_messages and hasattr(overwrite, "send_messages") and getattr(overwrite, "send_messages") is not True:
        overwrite.send_messages = True
        changed = True
    if disable_threads:
        for perm_name in ("create_public_threads", "create_private_threads", "send_messages_in_threads"):
            if hasattr(overwrite, perm_name) and getattr(overwrite, perm_name) is not True:
                setattr(overwrite, perm_name, True)
                changed = True
    return changed


async def _check_staff_module_enabled(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message(
            embed=_embed("Server Only", "Use this command in a server.", discord.Color.red()),
            ephemeral=True,
        )
        return False
    if not await is_module_enabled(interaction.guild.id, "staff_utils"):
        await interaction.response.send_message(
            embed=_embed(
                "Module Disabled",
                "Staff utilities are disabled in this server. Enable with `/modules`.",
                discord.Color.orange(),
            ),
            ephemeral=True,
        )
        return False
    return True


class ConfirmCancelView(discord.ui.View):
    def __init__(self, user_id: int, on_confirm):
        super().__init__(timeout=120)
        self.user_id = user_id
        self._on_confirm = on_confirm

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command invoker can use this.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self._on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=_embed("Cancelled", "No action was applied.", discord.Color.light_grey()),
            view=None,
        )
        self.stop()


class PurgeStopView(discord.ui.View):
    def __init__(self, user_id: int, stop_event: asyncio.Event):
        super().__init__(timeout=900)
        self.user_id = user_id
        self.stop_event = stop_event

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the command invoker can stop this purge.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Emergency Stop", style=discord.ButtonStyle.danger, emoji="🛑")
    async def stop(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop_event.set()
        await interaction.response.edit_message(
            embed=_embed("Stopping Purge", "Emergency stop requested. Finishing current delete action.", discord.Color.orange()),
            view=self,
        )


class StaffUtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._history_lock = asyncio.Lock()
        self._lockdown_lock = asyncio.Lock()
        self._notify_cooldown: dict[tuple[int, int], float] = {}

    async def _is_locked_down(self, guild_id: int) -> Optional[dict[str, Any]]:
        data = _get_lockdown_data()
        row = _lockdown_guild_row(data, guild_id)
        if not isinstance(row, dict) or not row.get("active", False):
            return None
        return row

    async def _retry_delete(self, message: discord.Message) -> bool:
        for _ in range(4):
            try:
                await message.delete()
                return True
            except discord.HTTPException as exc:
                retry_after = float(getattr(exc, "retry_after", 0.0) or 0.0)
                if retry_after > 0:
                    await asyncio.sleep(retry_after + 0.25)
                    continue
                return False
            except discord.Forbidden:
                return False
        return False

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        lockdown = await self._is_locked_down(message.guild.id)
        if lockdown is None:
            return
        if _staff_bypass(message.author):
            return
        excluded = {int(x) for x in lockdown.get("excluded_channel_ids", []) if str(x).isdigit()}
        if message.channel.id in excluded:
            return

        settings = lockdown.get("settings", {})
        should_delete = False
        if bool(settings.get("disable_messages", False)):
            should_delete = True
        if bool(settings.get("block_invites", False)) and INVITE_REGEX.search(message.content or ""):
            should_delete = True

        if not should_delete:
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            return

        custom_message = str(settings.get("custom_message") or "").strip()
        if not custom_message:
            return
        key = (message.guild.id, message.author.id)
        now = asyncio.get_running_loop().time()
        if now - self._notify_cooldown.get(key, 0.0) < LOCKDOWN_MESSAGE_COOLDOWN_SECONDS:
            return
        self._notify_cooldown[key] = now
        try:
            await message.author.send(custom_message)
        except (discord.Forbidden, discord.HTTPException):
            pass

    @app_commands.command(name="purge", description="Advanced purge with filters and confirmation.")
    @app_commands.describe(
        limit="How many recent messages to scan (1-1000)",
        user="Only messages by this user",
        contains="Only messages containing this text",
        bots_only="Only bot messages",
        links_only="Only messages containing links",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: (i.guild_id, i.user.id))
    async def purge(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, 1000],
        user: Optional[discord.Member] = None,
        contains: Optional[str] = None,
        bots_only: bool = False,
        links_only: bool = False,
    ) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.channel is None or not isinstance(interaction.channel, discord.TextChannel):
            await interaction.response.send_message(
                embed=_embed("Unsupported Channel", "Use this in a text channel.", discord.Color.red()),
                ephemeral=True,
            )
            return

        contains_lc = (contains or "").lower().strip()
        candidates: list[discord.Message] = []
        scanned = 0
        async for msg in interaction.channel.history(limit=limit, oldest_first=False):
            scanned += 1
            if user and msg.author.id != user.id:
                continue
            if bots_only and not msg.author.bot:
                continue
            if links_only and not INVITE_REGEX.search(msg.content or "") and "http://" not in (msg.content or "").lower() and "https://" not in (msg.content or "").lower():
                continue
            if contains_lc and contains_lc not in (msg.content or "").lower():
                continue
            candidates.append(msg)

        lines = [
            f"Scanned: **{scanned}**",
            f"Matched: **{len(candidates)}**",
            f"Channel: {interaction.channel.mention}",
        ]
        if user:
            lines.append(f"User filter: {user.mention}")
        if contains_lc:
            lines.append(f"Contains: `{_shorten(contains_lc, 80)}`")
        if bots_only:
            lines.append("Bots only: **Yes**")
        if links_only:
            lines.append("Links only: **Yes**")

        if not candidates:
            await interaction.response.send_message(
                embed=_embed("Purge Preview", "\n".join(lines) + "\n\nNo messages matched.", discord.Color.orange()),
                ephemeral=True,
            )
            return

        async def _confirm_purge(confirm_interaction: discord.Interaction) -> None:
            stop_event = asyncio.Event()
            view = PurgeStopView(confirm_interaction.user.id, stop_event)
            await confirm_interaction.response.edit_message(
                embed=_embed("Purge Running", "Deleting matched messages…", discord.Color.orange()),
                view=view,
            )
            deleted = 0
            for idx, msg in enumerate(candidates, start=1):
                if stop_event.is_set():
                    break
                if await self._retry_delete(msg):
                    deleted += 1
                if idx % 20 == 0:
                    await confirm_interaction.edit_original_response(
                        embed=_embed("Purge Running", f"Processed **{idx}/{len(candidates)}** · Deleted **{deleted}**", discord.Color.orange()),
                        view=view,
                    )
                await asyncio.sleep(PURGE_DELETE_DELAY_SECONDS)
            status = "stopped early" if stop_event.is_set() else "completed"
            await confirm_interaction.edit_original_response(
                embed=_embed(
                    "Purge Complete",
                    f"Purge {status}. Deleted **{deleted}** of **{len(candidates)}** matched messages.",
                    discord.Color.green(),
                ),
                view=None,
            )
            print(f"[staff_utils] purge guild={interaction.guild_id} channel={interaction.channel_id} deleted={deleted}")

        preview = _embed(
            "Purge Preview",
            "\n".join(lines) + "\n\nPress **Confirm** to delete matched messages.",
            discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=preview, view=ConfirmCancelView(interaction.user.id, _confirm_purge), ephemeral=True)

    @app_commands.command(name="lock", description="Lock a channel for @everyone (send messages off).")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=_embed("Invalid Channel", "Choose a text channel.", discord.Color.red()), ephemeral=True)
            return
        overwrite = target.overwrites_for(target.guild.default_role)
        overwrite.send_messages = False
        try:
            await target.set_permissions(target.guild.default_role, overwrite=overwrite, reason=f"Locked by {interaction.user}")
            await interaction.response.send_message(embed=_embed("Channel Locked", f"{target.mention} is now locked.", discord.Color.green()), ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message(embed=_embed("Lock Failed", f"Could not lock channel: `{type(exc).__name__}`", discord.Color.red()), ephemeral=True)

    @app_commands.command(name="unlock", description="Unlock a channel for @everyone.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=_embed("Invalid Channel", "Choose a text channel.", discord.Color.red()), ephemeral=True)
            return
        overwrite = target.overwrites_for(target.guild.default_role)
        overwrite.send_messages = None
        try:
            await target.set_permissions(target.guild.default_role, overwrite=overwrite, reason=f"Unlocked by {interaction.user}")
            await interaction.response.send_message(embed=_embed("Channel Unlocked", f"{target.mention} is now unlocked.", discord.Color.green()), ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message(embed=_embed("Unlock Failed", f"Could not unlock channel: `{type(exc).__name__}`", discord.Color.red()), ephemeral=True)

    @app_commands.command(name="slowmode", description="Set channel slowmode in seconds.")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    async def slowmode(
        self,
        interaction: discord.Interaction,
        time: app_commands.Range[int, 0, 21600],
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=_embed("Invalid Channel", "Choose a text channel.", discord.Color.red()), ephemeral=True)
            return
        try:
            await target.edit(slowmode_delay=int(time), reason=f"Slowmode set by {interaction.user}")
            await interaction.response.send_message(embed=_embed("Slowmode Updated", f"{target.mention} slowmode set to **{time}s**.", discord.Color.green()), ephemeral=True)
        except (discord.Forbidden, discord.HTTPException) as exc:
            await interaction.response.send_message(embed=_embed("Slowmode Failed", f"Could not update slowmode: `{type(exc).__name__}`", discord.Color.red()), ephemeral=True)

    @app_commands.command(name="nuke", description="Delete and recreate a channel (with confirmation).")
    @app_commands.default_permissions(manage_channels=True)
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: (i.guild_id, i.user.id))
    async def nuke(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message(embed=_embed("Invalid Channel", "Choose a text channel.", discord.Color.red()), ephemeral=True)
            return

        async def _confirm_nuke(confirm_interaction: discord.Interaction) -> None:
            try:
                clone = await target.clone(reason=f"Nuked by {confirm_interaction.user}")
                await clone.edit(position=target.position, category=target.category)
                await target.delete(reason=f"Nuked by {confirm_interaction.user}")
                await confirm_interaction.response.edit_message(
                    embed=_embed("Channel Nuked", f"Recreated channel: {clone.mention}", discord.Color.green()),
                    view=None,
                )
            except (discord.Forbidden, discord.HTTPException) as exc:
                await confirm_interaction.response.edit_message(
                    embed=_embed("Nuke Failed", f"Could not nuke channel: `{type(exc).__name__}`", discord.Color.red()),
                    view=None,
                )

        await interaction.response.send_message(
            embed=_embed(
                "Confirm Nuke",
                f"This will delete and recreate {target.mention}.\nThis is destructive.",
                discord.Color.red(),
            ),
            view=ConfirmCancelView(interaction.user.id, _confirm_nuke),
            ephemeral=True,
        )

    @app_commands.command(name="giverole", description="Give a role to all/humans/bots with progress feedback.")
    @app_commands.describe(target_type="Who receives the role", role="Role to assign")
    @app_commands.choices(target_type=TARGET_TYPE_CHOICES)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.checks.cooldown(1, 8.0, key=lambda i: (i.guild_id, i.user.id))
    async def giverole(
        self,
        interaction: discord.Interaction,
        target_type: app_commands.Choice[str],
        role: discord.Role,
    ) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        bot_member = interaction.guild.me
        if bot_member is None or bot_member.top_role <= role:
            await interaction.followup.send(
                embed=_embed("Role Hierarchy Error", "Bot role must be above the target role.", discord.Color.red()),
                ephemeral=True,
            )
            return

        members = list(interaction.guild.members)
        if target_type.value == "humans":
            members = [m for m in members if not m.bot]
        elif target_type.value == "bots":
            members = [m for m in members if m.bot]

        total = len(members)
        changed = 0
        skipped = 0
        failed = 0
        for idx, member in enumerate(members, start=1):
            if role in member.roles:
                skipped += 1
            elif bot_member.top_role <= member.top_role:
                skipped += 1
            else:
                try:
                    await member.add_roles(role, reason=f"Bulk giverole by {interaction.user}")
                    changed += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
            if idx % 25 == 0:
                await interaction.edit_original_response(
                    embed=_embed(
                        "Role Update Running",
                        f"Processed **{idx}/{total}**\nAdded: **{changed}** · Skipped: **{skipped}** · Failed: **{failed}**",
                        discord.Color.orange(),
                    )
                )
            await asyncio.sleep(ROLE_BULK_EDIT_DELAY_SECONDS)

        await interaction.edit_original_response(
            embed=_embed(
                "Bulk Give Role Complete",
                f"Role: {role.mention}\nTarget: **{target_type.value}**\nAdded: **{changed}** · Skipped: **{skipped}** · Failed: **{failed}**",
                discord.Color.green(),
            )
        )
        print(f"[staff_utils] giverole guild={interaction.guild_id} target={target_type.value} role={role.id} changed={changed}")

    @app_commands.command(name="removerole", description="Remove a role from all/humans/bots with progress feedback.")
    @app_commands.describe(target_type="Who loses the role", role="Role to remove")
    @app_commands.choices(target_type=TARGET_TYPE_CHOICES)
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.checks.has_permissions(manage_roles=True)
    @app_commands.checks.cooldown(1, 8.0, key=lambda i: (i.guild_id, i.user.id))
    async def removerole(
        self,
        interaction: discord.Interaction,
        target_type: app_commands.Choice[str],
        role: discord.Role,
    ) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        await interaction.response.defer(ephemeral=True)
        bot_member = interaction.guild.me
        if bot_member is None or bot_member.top_role <= role:
            await interaction.followup.send(
                embed=_embed("Role Hierarchy Error", "Bot role must be above the target role.", discord.Color.red()),
                ephemeral=True,
            )
            return

        members = list(interaction.guild.members)
        if target_type.value == "humans":
            members = [m for m in members if not m.bot]
        elif target_type.value == "bots":
            members = [m for m in members if m.bot]

        total = len(members)
        changed = 0
        skipped = 0
        failed = 0
        for idx, member in enumerate(members, start=1):
            if role not in member.roles:
                skipped += 1
            elif bot_member.top_role <= member.top_role:
                skipped += 1
            else:
                try:
                    await member.remove_roles(role, reason=f"Bulk removerole by {interaction.user}")
                    changed += 1
                except (discord.Forbidden, discord.HTTPException):
                    failed += 1
            if idx % 25 == 0:
                await interaction.edit_original_response(
                    embed=_embed(
                        "Role Update Running",
                        f"Processed **{idx}/{total}**\nRemoved: **{changed}** · Skipped: **{skipped}** · Failed: **{failed}**",
                        discord.Color.orange(),
                    )
                )
            await asyncio.sleep(ROLE_BULK_EDIT_DELAY_SECONDS)

        await interaction.edit_original_response(
            embed=_embed(
                "Bulk Remove Role Complete",
                f"Role: {role.mention}\nTarget: **{target_type.value}**\nRemoved: **{changed}** · Skipped: **{skipped}** · Failed: **{failed}**",
                discord.Color.green(),
            )
        )
        print(f"[staff_utils] removerole guild={interaction.guild_id} target={target_type.value} role={role.id} changed={changed}")

    @app_commands.command(name="userinfo", description="Show detailed user and moderation history information.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def userinfo(self, interaction: discord.Interaction, user: Optional[discord.Member] = None) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        target = user or interaction.user
        if not isinstance(target, discord.Member):
            await interaction.response.send_message(
                embed=_embed("Unavailable", "Could not resolve that member in this server.", discord.Color.red()),
                ephemeral=True,
            )
            return

        async with self._history_lock:
            history = _get_history_data()
        notes = _user_history_row(history, interaction.guild.id, target.id).get("notes", [])
        warns = _warn_entries(interaction.guild.id, target.id)
        roles = [r.mention for r in target.roles if r.name != "@everyone"]
        roles_text = ", ".join(roles)
        if not roles_text:
            roles_text = "No roles"
        if len(roles_text) > MAX_ROLE_LIST_CHARS:
            roles_text = roles_text[: MAX_ROLE_LIST_CHARS - 1] + "…"

        em = _embed(
            "User Information",
            f"{target.mention} (`{target.id}`)",
            discord.Color.blurple(),
        )
        em.set_thumbnail(url=target.display_avatar.url)
        em.add_field(name="Account Created", value=_fmt_ts(target.created_at.timestamp()), inline=True)
        em.add_field(name="Joined Server", value=_fmt_ts(target.joined_at.timestamp() if target.joined_at else None), inline=True)
        em.add_field(name="Top Role", value=target.top_role.mention if target.top_role else "None", inline=True)
        em.add_field(name="Warnings", value=str(len(warns)), inline=True)
        em.add_field(name="Notes", value=str(len(notes)), inline=True)
        em.add_field(name="Roles", value=roles_text, inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    async def _apply_lockdown(
        self,
        interaction: discord.Interaction,
        cutoff_role: discord.Role,
        hide_channels: bool,
        disable_messages: bool,
        disable_threads: bool,
        block_invites: bool,
        custom_message: str | None,
        excluded_ids: set[int],
    ) -> tuple[int, int, int]:
        """
        Apply lockdown to @everyone and every role at or below ``cutoff_role`` (inclusive).
        Roles above the cutoff get explicit allows so mods/admins keep access.
        Returns (channels_touched, channels_scanned, permission_edits_applied).
        """
        if interaction.guild is None:
            return (0, 0, 0)
        guild = interaction.guild
        locked_roles = _locked_roles_for_cutoff(guild, cutoff_role)
        exempt_roles = _exempt_roles_for_cutoff(guild, cutoff_role)
        bot_member = guild.me
        bot_top = bot_member.top_role if bot_member else None
        if bot_top is not None and bot_top not in exempt_roles:
            exempt_roles = [bot_top] + exempt_roles

        channels_touched = 0
        scanned = 0
        edits = 0
        channel_state: dict[str, dict[str, Any]] = {}

        restrict_channel_perms = hide_channels or disable_messages or disable_threads

        for channel in guild.channels:
            if channel.id in excluded_ids:
                continue
            if not isinstance(channel, discord.abc.GuildChannel):
                continue
            scanned += 1
            roles_snap: dict[str, dict[str, Optional[bool]]] = {}
            channel_modified = False

            for role in locked_roles:
                if bot_top is not None and role == bot_top:
                    continue
                overwrite = channel.overwrites_for(role)
                before = _snapshot_lockdown_overwrites(overwrite)
                if not _apply_lockdown_denies_to_overwrite(
                    overwrite,
                    hide_channels=hide_channels,
                    disable_messages=disable_messages,
                    disable_threads=disable_threads,
                ):
                    continue
                try:
                    await channel.set_permissions(role, overwrite=overwrite, reason=f"Lockdown by {interaction.user}")
                    roles_snap[str(role.id)] = before
                    channel_modified = True
                    edits += 1
                except (discord.Forbidden, discord.HTTPException):
                    pass
                await asyncio.sleep(LOCKDOWN_EDIT_DELAY_SECONDS)

            if restrict_channel_perms:
                for role in exempt_roles:
                    overwrite = channel.overwrites_for(role)
                    before = _snapshot_lockdown_overwrites(overwrite)
                    if not _apply_lockdown_allows_to_overwrite(
                        overwrite,
                        hide_channels=hide_channels,
                        disable_messages=disable_messages,
                        disable_threads=disable_threads,
                    ):
                        continue
                    try:
                        await channel.set_permissions(role, overwrite=overwrite, reason=f"Lockdown bypass for {role.name}")
                        roles_snap[str(role.id)] = before
                        channel_modified = True
                        edits += 1
                    except (discord.Forbidden, discord.HTTPException):
                        pass
                    await asyncio.sleep(LOCKDOWN_EDIT_DELAY_SECONDS)

            if channel_modified and roles_snap:
                channel_state[str(channel.id)] = {"roles": roles_snap}
                channels_touched += 1

        async with self._lockdown_lock:
            data = _get_lockdown_data()
            guilds = data.setdefault("guilds", {})
            guilds[str(guild.id)] = {
                "active": True,
                "started_at": _now_iso(),
                "started_by": interaction.user.id,
                "cutoff_role_id": cutoff_role.id,
                "excluded_channel_ids": sorted(excluded_ids),
                "settings": {
                    "hide_channels": hide_channels,
                    "disable_messages": disable_messages,
                    "disable_threads": disable_threads,
                    "block_invites": block_invites,
                    "custom_message": custom_message or "",
                },
                "channels": channel_state,
            }
            _save_lockdown_data(data)

        return channels_touched, scanned, edits

    @app_commands.command(name="lockdown", description="Apply emergency lockdown restrictions to this server.")
    @app_commands.describe(
        cutoff_role=(
            "Lock applies to @everyone and every role at or below this role. "
            "Roles above keep access (explicit allow)."
        ),
        hide_channels="Hide channels from locked roles",
        disable_messages="Disable sending messages for locked roles",
        disable_threads="Disable thread creation/sending for locked roles",
        block_invites="Delete Discord invite links while lockdown is active",
        custom_message="Message sent (DM) when a member is blocked",
        excluded_channel_1="Optional channel exclusion",
        excluded_channel_2="Optional channel exclusion",
        excluded_channel_3="Optional channel exclusion",
        excluded_channel_4="Optional channel exclusion",
        excluded_channel_5="Optional channel exclusion",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: (i.guild_id, i.user.id))
    async def lockdown(
        self,
        interaction: discord.Interaction,
        cutoff_role: discord.Role,
        hide_channels: bool = False,
        disable_messages: bool = True,
        disable_threads: bool = True,
        block_invites: bool = True,
        custom_message: Optional[str] = None,
        excluded_channel_1: Optional[discord.TextChannel] = None,
        excluded_channel_2: Optional[discord.TextChannel] = None,
        excluded_channel_3: Optional[discord.TextChannel] = None,
        excluded_channel_4: Optional[discord.TextChannel] = None,
        excluded_channel_5: Optional[discord.TextChannel] = None,
    ) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        active = await self._is_locked_down(interaction.guild.id)
        if active is not None:
            await interaction.response.send_message(
                embed=_embed("Lockdown Already Active", "Run `/unlockdown` before applying again.", discord.Color.orange()),
                ephemeral=True,
            )
            return

        excluded_ids = {
            ch.id
            for ch in [
                excluded_channel_1,
                excluded_channel_2,
                excluded_channel_3,
                excluded_channel_4,
                excluded_channel_5,
            ]
            if ch is not None
        }

        async def _confirm_lockdown(confirm_interaction: discord.Interaction) -> None:
            await confirm_interaction.response.edit_message(
                embed=_embed("Applying Lockdown", "Applying permission overrides…", discord.Color.orange()),
                view=None,
            )
            touched, scanned, edits = await self._apply_lockdown(
                interaction=confirm_interaction,
                cutoff_role=cutoff_role,
                hide_channels=hide_channels,
                disable_messages=disable_messages,
                disable_threads=disable_threads,
                block_invites=block_invites,
                custom_message=custom_message,
                excluded_ids=excluded_ids,
            )
            summary = [
                f"Cutoff role: {cutoff_role.mention} (locks this role **and every role below** it)",
                f"Channels with permission changes: **{touched}** / scanned **{scanned}**",
                f"Permission edits applied: **{edits}**",
                f"hide_channels: **{hide_channels}**",
                f"disable_messages: **{disable_messages}**",
                f"disable_threads: **{disable_threads}**",
                f"block_invites: **{block_invites}**",
                f"excluded_channels: **{len(excluded_ids)}**",
            ]
            await confirm_interaction.edit_original_response(
                embed=_embed("Lockdown Active", "\n".join(summary), discord.Color.red()),
                view=None,
            )
            print(f"[staff_utils] lockdown guild={interaction.guild_id} touched={touched} edits={edits}")

        preview = _embed(
            "Confirm Lockdown",
            (
                "This applies restrictions to **@everyone** and **every role at or below** your cutoff role.\n"
                f"Cutoff: {cutoff_role.mention}\n"
                "**Roles above** the cutoff get explicit **Allow** so staff are not locked out.\n"
                "Excluded channels are skipped."
            ),
            discord.Color.red(),
        )
        await interaction.response.send_message(embed=preview, view=ConfirmCancelView(interaction.user.id, _confirm_lockdown), ephemeral=True)

    @app_commands.command(name="unlockdown", description="Restore channel permissions saved by /lockdown.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def unlockdown(self, interaction: discord.Interaction) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        guild = interaction.guild

        async with self._lockdown_lock:
            data = _get_lockdown_data()
            row = _lockdown_guild_row(data, guild.id)
            if not isinstance(row, dict) or not row.get("active", False):
                await interaction.response.send_message(
                    embed=_embed("No Lockdown Active", "There is no active lockdown to restore.", discord.Color.orange()),
                    ephemeral=True,
                )
                return

            await interaction.response.defer(ephemeral=True)
            restored = 0
            channel_state = row.get("channels", {})
            if isinstance(channel_state, dict):
                for channel_id, payload in channel_state.items():
                    channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
                    if channel is None or not isinstance(channel, discord.abc.GuildChannel) or not isinstance(payload, dict):
                        continue
                    if isinstance(payload.get("roles"), dict):
                        roles_snapshot: dict[str, Any] = payload["roles"]
                    else:
                        roles_snapshot = {str(guild.default_role.id): payload}

                    for rid_str, perm_snap in roles_snapshot.items():
                        if not isinstance(perm_snap, dict):
                            continue
                        if not str(rid_str).isdigit():
                            continue
                        role = guild.get_role(int(rid_str))
                        if role is None:
                            continue
                        overwrite = channel.overwrites_for(role)
                        for perm_name in LOCKDOWN_PERMISSIONS:
                            if perm_name in perm_snap:
                                setattr(overwrite, perm_name, perm_snap.get(perm_name))
                        try:
                            await channel.set_permissions(role, overwrite=overwrite, reason=f"Unlockdown by {interaction.user}")
                            restored += 1
                        except (discord.Forbidden, discord.HTTPException):
                            pass
                        await asyncio.sleep(LOCKDOWN_EDIT_DELAY_SECONDS)

            data.get("guilds", {}).pop(str(guild.id), None)
            _save_lockdown_data(data)

        await interaction.followup.send(
            embed=_embed(
                "Lockdown Removed",
                f"Restored **{restored}** role/channel permission overwrite(s) from the saved snapshot.",
                discord.Color.green(),
            ),
            ephemeral=True,
        )
        print(f"[staff_utils] unlockdown guild={interaction.guild_id} restored={restored}")


class HistoryCog(commands.GroupCog, group_name="history", group_description="View moderation history"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._history_lock = asyncio.Lock()

    @app_commands.command(name="user", description="Show warning + note history for a member.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def history_user(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        async with self._history_lock:
            history = _get_history_data()
        notes = _user_history_row(history, interaction.guild.id, user.id).get("notes", [])
        warns = _warn_entries(interaction.guild.id, user.id)

        warn_lines = [
            f"- {_shorten(str(w.get('reason', 'No reason')), 90)} · {_fmt_ts(w.get('timestamp'))}"
            for w in warns[-MAX_HISTORY_LINES:]
            if isinstance(w, dict)
        ]
        note_lines = [
            f"- {_shorten(str(n.get('text', '')), 90)} · {_fmt_ts(n.get('timestamp'))}"
            for n in notes[-MAX_HISTORY_LINES:]
            if isinstance(n, dict)
        ]
        em = _embed(
            "User History",
            f"History for {user.mention} (`{user.id}`)",
            discord.Color.blurple(),
        )
        em.add_field(name=f"Warnings ({len(warns)})", value="\n".join(warn_lines) if warn_lines else "No warnings recorded.", inline=False)
        em.add_field(name=f"Notes ({len(notes)})", value="\n".join(note_lines) if note_lines else "No notes recorded.", inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)


class NoteCog(commands.GroupCog, group_name="note", group_description="Manage internal staff notes"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._history_lock = asyncio.Lock()

    @app_commands.command(name="add", description="Add a staff note for a user.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def note_add(self, interaction: discord.Interaction, user: discord.Member, text: str) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        note_text = text.strip()
        if not note_text:
            await interaction.response.send_message(
                embed=_embed("Invalid Note", "Note text cannot be empty.", discord.Color.red()),
                ephemeral=True,
            )
            return
        async with self._history_lock:
            data = _get_history_data()
            row = _user_history_row(data, interaction.guild.id, user.id)
            notes = row.setdefault("notes", [])
            if not isinstance(notes, list):
                notes = []
                row["notes"] = notes
            notes.append(
                {
                    "text": note_text,
                    "timestamp": int(discord.utils.utcnow().timestamp()),
                    "moderator_id": interaction.user.id,
                }
            )
            _save_history_data(data)
        await interaction.response.send_message(
            embed=_embed("Note Added", f"Saved note for {user.mention}.", discord.Color.green()),
            ephemeral=True,
        )
        print(f"[staff_utils] note_add guild={interaction.guild_id} target={user.id} by={interaction.user.id}")

    @app_commands.command(name="view", description="View staff notes for a user.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    async def note_view(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if not await _check_staff_module_enabled(interaction):
            return
        if interaction.guild is None:
            return
        async with self._history_lock:
            data = _get_history_data()
        notes = _user_history_row(data, interaction.guild.id, user.id).get("notes", [])
        lines = [
            f"- {_shorten(str(n.get('text', '')), 100)} · {_fmt_ts(n.get('timestamp'))} · <@{n.get('moderator_id', 0)}>"
            for n in notes[-MAX_HISTORY_LINES:]
            if isinstance(n, dict)
        ]
        await interaction.response.send_message(
            embed=_embed(
                "User Notes",
                f"{user.mention} has **{len(notes)}** note(s).\n\n" + ("\n".join(lines) if lines else "No notes."),
                discord.Color.blurple(),
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StaffUtilityCog(bot))
    await bot.add_cog(HistoryCog(bot))
    await bot.add_cog(NoteCog(bot))

