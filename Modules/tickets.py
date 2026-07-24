# ================== TICKETS SYSTEM ==================
# Import bot and tree from the main script (must be imported after bot/tree are defined)
import sys
import os
import json
import asyncio
import re
import discord
from discord import Interaction
from discord.ui import View, Button, Select
import discord.ui as ui
from typing import Any
from Modules.i18n import t_sync

_main = sys.modules.get("__main__")
if _main and hasattr(_main, "bot") and hasattr(_main, "tree"):
    bot = _main.bot
    tree = _main.tree
else:
    raise RuntimeError(
        "tickets.py must be imported from the main bot script after bot and tree are defined"
    )

from Modules import json_cache
from Modules.themes import get_command_response, get_command_response_for_interaction

_TICKETS_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Storage")
TICKETS_FILE = os.path.join(_TICKETS_BASE, "Data", "tickets.json")
_TRANSCRIPTS_DIR = os.path.join(_TICKETS_BASE, "Data", "ticket_transcripts")
os.makedirs(_TRANSCRIPTS_DIR, exist_ok=True)
TICKETS_I18N_PREFIX = "tickets."
ticket_group = discord.app_commands.Group(
    name="ticket",
    description="Ticket system commands",
)


def _ticket_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{TICKETS_I18N_PREFIX}{key}", default=default, **params)


def load_json(path, default=None):
    return json_cache.get(path, default if default is not None else {})


def save_json(path, data):
    json_cache.set_(path, data)


def _dispatch_ticket_event(
    guild: discord.Guild | None,
    actor: discord.abc.User | None,
    action: str,
    channel_id: int,
    details: str = "",
):
    if guild is None or actor is None:
        return
    try:
        bot.dispatch("coffeecord_ticket_event", guild, actor, action, channel_id, details)
    except Exception:
        pass


# ---------- PERSISTENT VIEWS (call from main on_ready) ----------
def register_persistent_views(bot_instance):
    """Call this from your main on_ready to restore ticket panels after restart."""
    data = load_json(TICKETS_FILE, {})
    for guild_id, guild_data in data.items():
        bot_instance.add_view(TicketPanel(guild_id))
        panel_mid = guild_data.get("panel_message_id")
        if panel_mid is not None and str(panel_mid).isdigit():
            bot_instance.add_view(TicketPanel(guild_id), message_id=int(panel_mid))
        for channel_id in guild_data.get("tickets", {}).keys():
            bot_instance.add_view(TicketControlPanel(guild_id, int(channel_id)))
    print("✅ Persistent ticket views registered")


async def republish_ticket_panel(
    bot_instance,
    guild: discord.Guild,
    *,
    force: bool = False,
) -> str | None:
    """Post a ticket panel after backup restore.

    In repair mode (force=False), keep an existing panel message when it still
    resolves; only create a new one if missing.
    """
    data = load_json(TICKETS_FILE, {})
    guild_id = str(guild.id)
    guild_data = data.get(guild_id)
    if not isinstance(guild_data, dict):
        return None
    channel_id = guild_data.get("ticket_channel")
    if channel_id is None or not str(channel_id).isdigit():
        return "tickets: no ticket_channel configured"
    channel = guild.get_channel(int(channel_id))
    if not isinstance(channel, discord.TextChannel):
        return f"tickets: channel `{channel_id}` missing after restore"

    # Keep only open tickets whose channels still exist.
    open_tickets = guild_data.get("tickets") or {}
    cleaned: dict[str, Any] = {}
    if isinstance(open_tickets, dict):
        for cid, info in open_tickets.items():
            if not str(cid).isdigit():
                continue
            if guild.get_channel(int(cid)) is None:
                continue
            cleaned[str(cid)] = info
            try:
                bot_instance.add_view(TicketControlPanel(guild_id, int(cid)))
            except Exception:
                pass
    guild_data["tickets"] = cleaned

    select_prefix = f"ticket_select_{guild_id}"
    create_prefix = f"ticket_create_{guild_id}_"

    def _is_ticket_panel_message(msg: discord.Message) -> bool:
        for row in msg.components:
            for child in row.children:
                custom_id = getattr(child, "custom_id", None)
                if not isinstance(custom_id, str):
                    continue
                if custom_id == select_prefix or custom_id.startswith(create_prefix):
                    return True
        return False

    async def _keep_existing(existing: discord.Message) -> str:
        try:
            bot_instance.add_view(TicketPanel(guild_id))
            bot_instance.add_view(TicketPanel(guild_id), message_id=existing.id)
        except Exception:
            pass
        guild_data["panel_message_id"] = existing.id
        data[guild_id] = guild_data
        save_json(TICKETS_FILE, data)
        return f"tickets: existing panel kept in #{channel.name}"

    if not force:
        panel_mid = guild_data.get("panel_message_id")
        if panel_mid is not None and str(panel_mid).isdigit():
            try:
                existing = await channel.fetch_message(int(panel_mid))
                if _is_ticket_panel_message(existing):
                    return await _keep_existing(existing)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        try:
            async for msg in channel.history(limit=50):
                if _is_ticket_panel_message(msg):
                    return await _keep_existing(msg)
        except (discord.Forbidden, discord.HTTPException):
            pass

    message = str(guild_data.get("ticket_message") or "Click below to create a ticket.")
    embed = discord.Embed(
        title=_ticket_text_sync(guild.id, "panel_title", default="🎫 Support Tickets"),
        description=message,
        color=discord.Color.blue(),
    )
    view = TicketPanel(guild_id)
    panel_msg = await channel.send(embed=embed, view=view)
    guild_data["panel_message_id"] = panel_msg.id
    data[guild_id] = guild_data
    save_json(TICKETS_FILE, data)
    try:
        bot_instance.add_view(TicketPanel(guild_id))
        bot_instance.add_view(TicketPanel(guild_id), message_id=panel_msg.id)
    except Exception:
        pass
    return f"tickets: panel republished in #{channel.name}"


# ---------- /ticket_setup ----------
@ticket_group.command(
    name="setup",
    description="Set up the ticket system",
)
async def ticket_setup(
    interaction: Interaction,
    channel: discord.TextChannel,
    support_roles: str,
    ticket_type1: str,
    ticket_type2: str = None,
    ticket_type3: str = None,
    message: str = "Click below to create a ticket."
):
    guild_id = str(interaction.guild.id)

    roles = []
    # Accept role mentions/IDs separated by commas, spaces, or newlines.
    # Examples:
    # "<@&1>,<@&2>"  |  "<@&1> <@&2>"  |  "1 2"
    role_tokens = re.findall(r"<@&(\d+)>|(\d+)", support_roles)
    role_ids: list[int] = []
    for mention_id, raw_id in role_tokens:
        role_ids.append(int(mention_id or raw_id))

    if not role_ids:
        await interaction.response.send_message(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "setup_no_valid_roles",
                default="❌ No valid support roles found. Use role mentions like `@Role` or raw role IDs.",
            ),
            ephemeral=True,
        )
        return

    seen = set()
    for rid in role_ids:
        if rid in seen:
            continue
        seen.add(rid)
        role = interaction.guild.get_role(rid)
        if role:
            roles.append(role)

    if not roles:
        await interaction.response.send_message(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "setup_roles_not_found",
                default="❌ None of the provided roles were found in this server.",
            ),
            ephemeral=True,
        )
        return

    ticket_types = [ticket_type1]
    if ticket_type2:
        ticket_types.append(ticket_type2)
    if ticket_type3:
        ticket_types.append(ticket_type3)

    data = load_json(TICKETS_FILE, {})
    data[guild_id] = {
        "ticket_channel": channel.id,
        "support_roles": [r.id for r in roles],
        "ticket_types": ticket_types,
        "ticket_message": message,
        "tickets": {},
    }

    embed = discord.Embed(
        title=_ticket_text_sync(
            interaction.guild.id if interaction.guild else None,
            "panel_title",
            default="🎫 Support Tickets",
        ),
        description=message,
        color=discord.Color.blue()
    )

    panel_msg = await channel.send(embed=embed, view=TicketPanel(guild_id))
    data[guild_id]["panel_message_id"] = panel_msg.id
    save_json(TICKETS_FILE, data)
    try:
        bot.add_view(TicketPanel(guild_id))
        bot.add_view(TicketPanel(guild_id), message_id=panel_msg.id)
    except Exception:
        pass
    _dispatch_ticket_event(
        interaction.guild,
        interaction.user,
        "setup",
        channel.id,
        f"Support roles: {len(roles)} | Types: {', '.join(ticket_types)}",
    )
    msg = get_command_response_for_interaction(
        interaction,
        "success",
        _ticket_text_sync(
            interaction.guild.id if interaction.guild else None,
            "setup_success",
            default="✅ Ticket system set up!",
        ),
    )
    await interaction.response.send_message(msg, ephemeral=True)


# ---------- TICKET PANEL ----------
class TicketPanel(ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        data = load_json(TICKETS_FILE, {})
        ticket_types = data.get(str(guild_id), {}).get("ticket_types", [])

        if len(ticket_types) > 1:
            self.add_item(TicketTypeSelect(ticket_types, guild_id))
        elif ticket_types:
            self.add_item(CreateTicketButton(ticket_types[0], guild_id))


class TicketTypeSelect(ui.Select):
    def __init__(self, ticket_types, guild_id):
        options = [
            discord.SelectOption(
                label=t,
                description=_ticket_text_sync(
                    int(guild_id),
                    "select_option_description",
                    default="Create a {ticket_type} ticket",
                    ticket_type=t,
                ),
            )
            for t in ticket_types
        ]
        super().__init__(
            placeholder=_ticket_text_sync(
                int(guild_id),
                "select_placeholder",
                default="Select ticket type",
            ),
            options=options,
            custom_id=f"ticket_select_{guild_id}"
        )
        self.guild_id = guild_id

    async def callback(self, interaction: Interaction):
        await create_ticket(interaction, self.values[0], str(self.guild_id))


class CreateTicketButton(ui.Button):
    def __init__(self, ticket_type, guild_id):
        super().__init__(
            label=_ticket_text_sync(
                int(guild_id),
                "create_button_label",
                default="Create {ticket_type}",
                ticket_type=ticket_type,
            ),
            style=discord.ButtonStyle.success,
            custom_id=f"ticket_create_{guild_id}_{ticket_type}"
        )
        self.ticket_type = ticket_type
        self.guild_id = guild_id

    async def callback(self, interaction: Interaction):
        await create_ticket(interaction, self.ticket_type, str(self.guild_id))


# ---------- CREATE TICKET ----------
async def create_ticket(interaction: Interaction, ticket_type: str, guild_id: str):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    member = interaction.user

    try:
        data = load_json(TICKETS_FILE, {})
        cfg = data.get(guild_id)
        if not cfg:
            await interaction.followup.send(
                _ticket_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "not_setup",
                    default="❌ Ticket system not set up for this server.",
                ),
                ephemeral=True,
            )
            return

        support_roles = cfg.get("support_roles", [])

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            member: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True)
        }

        for rid in support_roles:
            role = guild.get_role(rid)
            if role:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=f"{ticket_type.lower()}-{member.name}",
            overwrites=overwrites
        )

        cfg.setdefault("tickets", {})[str(channel.id)] = {
            "user": member.id,
            "type": ticket_type,
            "status": "open",
            "claimed_by": None
        }
        save_json(TICKETS_FILE, data)

        embed = discord.Embed(
            title=_ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "ticket_channel_title",
                default="{ticket_type} Ticket",
                ticket_type=ticket_type,
            ),
            description=cfg.get(
                "ticket_message",
                _ticket_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "default_panel_message",
                    default="Click below to create a ticket.",
                ),
            ),
            color=discord.Color.green()
        )

        await channel.send(embed=embed, view=TicketControlPanel(guild_id, channel.id))
        try:
            bot.add_view(TicketControlPanel(guild_id, channel.id))
        except Exception:
            pass
        _dispatch_ticket_event(
            interaction.guild,
            interaction.user,
            "create",
            channel.id,
            f"type={ticket_type}",
        )
        msg = get_command_response(
            guild.id,
            "ticket_create",
            "success",
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "create_success",
                default="✅ Ticket created: {channel}",
            ),
            channel=channel.mention,
        )
        await interaction.followup.send(msg, ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "create_missing_permissions",
                default="❌ I don't have permission to create channels.",
            ),
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "create_failed",
                default="❌ Failed to create ticket: {error}",
                error=str(e),
            ),
            ephemeral=True,
        )


# ---------- CONTROL PANEL ----------
class TicketControlPanel(ui.View):
    def __init__(self, guild_id, channel_id):
        super().__init__(timeout=None)
        self.add_item(ClaimButton(guild_id, channel_id))
        self.add_item(LockButton(guild_id, channel_id))
        self.add_item(UnlockButton(guild_id, channel_id))
       #self.add_item(CloseButton(guild_id))
        self.add_item(DeleteButton(guild_id, channel_id))


class ClaimButton(ui.Button):
    def __init__(self, guild_id, channel_id):
        super().__init__(
            label=_ticket_text_sync(int(guild_id), "claim_label", default="Claim"),
            style=discord.ButtonStyle.primary,
            custom_id=f"ticket_claim_{guild_id}_{channel_id}",
        )
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def callback(self, interaction: Interaction):
        data = load_json(TICKETS_FILE, {})
        ticket = data[str(self.guild_id)]["tickets"][str(self.channel_id)]
        ticket["claimed_by"] = interaction.user.id
        save_json(TICKETS_FILE, data)
        _dispatch_ticket_event(
            interaction.guild,
            interaction.user,
            "claim",
            self.channel_id,
            "",
        )
        await interaction.channel.send(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "claimed_by",
                default="🎟️ Claimed by {user}",
                user=interaction.user.mention,
            )
        )
        await interaction.response.defer()


class LockButton(ui.Button):
    def __init__(self, guild_id, channel_id):
        super().__init__(
            label=_ticket_text_sync(int(guild_id), "lock_label", default="Lock"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket_lock_{guild_id}_{channel_id}",
        )
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def callback(self, interaction: Interaction):
        data = load_json(TICKETS_FILE, {})
        ticket = data[str(self.guild_id)]["tickets"][str(self.channel_id)]
        user = interaction.guild.get_member(ticket["user"])
        await interaction.channel.set_permissions(user, send_messages=False)
        ticket["status"] = "locked"
        save_json(TICKETS_FILE, data)
        _dispatch_ticket_event(
            interaction.guild,
            interaction.user,
            "lock",
            self.channel_id,
            "",
        )
        await interaction.response.send_message(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "locked",
                default="🔒 Ticket locked",
            ),
            ephemeral=True,
        )


class UnlockButton(ui.Button):
    def __init__(self, guild_id, channel_id):
        super().__init__(
            label=_ticket_text_sync(int(guild_id), "unlock_label", default="Unlock"),
            style=discord.ButtonStyle.success,
            custom_id=f"ticket_unlock_{guild_id}_{channel_id}",
        )
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def callback(self, interaction: Interaction):
        data = load_json(TICKETS_FILE, {})
        ticket = data[str(self.guild_id)]["tickets"][str(self.channel_id)]
        user = interaction.guild.get_member(ticket["user"])
        await interaction.channel.set_permissions(user, send_messages=True)
        ticket["status"] = "open"
        save_json(TICKETS_FILE, data)
        _dispatch_ticket_event(
            interaction.guild,
            interaction.user,
            "unlock",
            self.channel_id,
            "",
        )
        await interaction.response.send_message(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "unlocked",
                default="🔓 Ticket unlocked",
            ),
            ephemeral=True,
        )


# class CloseButton(ui.Button):
#     def __init__(self, guild_id):
#         super().__init__(label="Close", style=discord.ButtonStyle.danger)
#         self.guild_id = guild_id

#     async def callback(self, interaction: Interaction):
#         data = load_json(TICKETS_FILE, {})
#         data[self.guild_id]["tickets"][str(interaction.channel.id)]["status"] = "closed"
#         save_json(TICKETS_FILE, data)
#         await interaction.channel.send("✅ Ticket closed")
#         await interaction.response.defer()


class DeleteButton(ui.Button):
    def __init__(self, guild_id, channel_id):
        super().__init__(
            label=_ticket_text_sync(int(guild_id), "close_label", default="Close"),
            style=discord.ButtonStyle.danger,
            custom_id=f"ticket_delete_{guild_id}_{channel_id}",
        )
        self.guild_id = guild_id
        self.channel_id = channel_id

    async def callback(self, interaction: Interaction):
        await interaction.response.send_message(
            _ticket_text_sync(
                interaction.guild.id if interaction.guild else None,
                "closing_soon",
                default="🗑️ Closing/Deleting in 5 seconds...",
            ),
            ephemeral=True,
        )
        channel = interaction.guild.get_channel(self.channel_id)
        if channel:
            await asyncio.sleep(5)
            data = load_json(TICKETS_FILE, {})
            data.setdefault(str(self.guild_id), {})
            data[str(self.guild_id)].setdefault("tickets", {})
            data[str(self.guild_id)]["tickets"].pop(str(self.channel_id), None)
            save_json(TICKETS_FILE, data)
            _dispatch_ticket_event(
                interaction.guild,
                interaction.user,
                "close_delete",
                self.channel_id,
                "",
            )
            await channel.delete()


# ---------- SLASH COMMANDS (PARAMETERS) ----------
@ticket_group.command(
    name="add",
    description="Add a user to this ticket",
)
async def ticket_add_user(interaction: Interaction, user: discord.Member):
    await interaction.channel.set_permissions(user, view_channel=True, send_messages=True)
    _dispatch_ticket_event(
        interaction.guild,
        interaction.user,
        "add_user",
        interaction.channel.id,
        f"user={user.id}",
    )
    await interaction.response.send_message(
        _ticket_text_sync(
            interaction.guild.id if interaction.guild else None,
            "add_user_success",
            default="✅ Added {user}",
            user=user.mention,
        ),
        ephemeral=True,
    )


@ticket_group.command(
    name="remove",
    description="Remove a user from this ticket",
)
async def ticket_remove_user(interaction: Interaction, user: discord.Member):
    await interaction.channel.set_permissions(user, overwrite=None)
    _dispatch_ticket_event(
        interaction.guild,
        interaction.user,
        "remove_user",
        interaction.channel.id,
        f"user={user.id}",
    )
    await interaction.response.send_message(
        _ticket_text_sync(
            interaction.guild.id if interaction.guild else None,
            "remove_user_success",
            default="🚫 Removed {user}",
            user=user.mention,
        ),
        ephemeral=True,
    )


async def setup(bot_instance):
    """Called by discord.py's load_extension — registers the ticket command group."""
    existing = tree.get_command("ticket")
    if existing is None:
        tree.add_command(ticket_group)
