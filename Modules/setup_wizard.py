import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.reactionrole import DEFAULT_PANEL_COLOR, _build_panel_embed
from Modules.welcome_leave import normalize_welcome_section


BASE_DIR = Path(__file__).resolve().parent.parent
WELCOME_LEAVE_PATH = BASE_DIR / "Storage" / "Config" / "welcome_leave.json"
LOGGING_PATH = BASE_DIR / "Storage" / "Config" / "logging.json"
AUTOMOD_PATH = BASE_DIR / "Storage" / "Config" / "automod.json"
REACTIONROLE_PATH = BASE_DIR / "Storage" / "Config" / "reactionrole_config.json"
TICKETS_PATH = BASE_DIR / "Storage" / "Data" / "tickets.json"

FEATURE_ORDER = ["welcome", "leave", "logging", "automod", "reaction_roles", "tickets"]
FEATURE_LABELS = {
    "welcome": "Welcome Messages",
    "leave": "Leave Messages",
    "logging": "Logging",
    "automod": "Automod",
    "reaction_roles": "Reaction Roles",
    "tickets": "Tickets",
}

LOGGING_EVENT_KEYS = [
    "message_delete",
    "message_edit",
    "member_join",
    "member_leave",
    "automod",
    "warn",
]


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fp:
            json.dump(default, fp, indent=2, ensure_ascii=True)
        return default
    try:
        with path.open("r", encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else default
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _default_draft() -> dict[str, Any]:
    return {
        "selected_features": [],
        "welcome": {
            "enabled": True,
            "channel_id": None,
            "message": "Welcome {user_mention} to {server_name}! We now have {member_count} members.",
            "embed_enabled": True,
        },
        "leave": {
            "enabled": True,
            "channel_id": None,
            "message": "Goodbye {user_name}. We're sad to see you go!",
            "embed_enabled": True,
            "exit_survey_enabled": False,
            "exit_survey_log_channel_id": None,
            "survey_mode": "preset",
        },
        "logging": {
            "enabled": True,
            "channel_id": None,
            "events": list(LOGGING_EVENT_KEYS),
        },
        "automod": {
            "enabled": True,
            "use_discord_automod": True,
            "use_bot_checks": True,
            "blocked_words": [],
            "match_type": "contains",
            "exempt_role_ids": [],
            "exempt_channel_id": None,
        },
        "reaction_roles": {
            "enabled": True,
            "channel_id": None,
            "message_text": "Pick your role:",
            "mode": "toggle",  # toggle | radio
            "role_ids": [],
            "emojis": [],
        },
        "tickets": {
            "enabled": True,
            "category_or_channel_id": None,
            "support_role_ids": [],
            "ticket_channel_id": None,
            "ticket_message": "Click below to create a ticket.",
            "button_label": "Open Ticket",
            "one_ticket_per_user": True,
        },
    }


@dataclass
class SetupSession:
    guild_id: int
    user_id: int
    draft: dict[str, Any] = field(default_factory=_default_draft)
    step_index: int = -1  # -1 means feature selection
    notice: str = ""
    message: Optional[discord.InteractionMessage] = None

    @property
    def selected(self) -> list[str]:
        raw = self.draft.get("selected_features", [])
        if not isinstance(raw, list):
            return []
        return [x for x in FEATURE_ORDER if x in raw]


class BaseOwnedView(discord.ui.View):
    def __init__(self, cog: "QuickSetupCog", session: SetupSession) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.session = session

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.session.user_id:
            await interaction.response.send_message(
                "This setup wizard belongs to another admin.",
                ephemeral=True,
            )
            return False
        return True


class MessageTemplateModal(discord.ui.Modal):
    def __init__(self, title: str, parent: "SetupStepView", draft_path: tuple[str, str], current: str):
        super().__init__(title=title, timeout=300)
        self.parent_view = parent
        self.section_key, self.message_key = draft_path
        self.template = discord.ui.TextInput(
            label="Message template",
            style=discord.TextStyle.paragraph,
            max_length=2000,
            required=True,
            default=current[:2000],
            placeholder="Use placeholders like {user_mention}, {server_name}",
        )
        self.add_item(self.template)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft[self.section_key][self.message_key] = str(self.template.value).strip()
        await interaction.response.defer()
        await self.parent_view.cog.redraw_from_modal(self.parent_view.session)


class CommaWordsModal(discord.ui.Modal):
    def __init__(self, parent: "SetupStepView", current_words: list[str]):
        super().__init__(title="Blocked Words", timeout=300)
        self.parent_view = parent
        self.words = discord.ui.TextInput(
            label="Comma-separated words",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1500,
            default=", ".join(current_words)[:1500],
            placeholder="word1, word2, phrase3",
        )
        self.add_item(self.words)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = [w.strip().lower() for w in str(self.words.value).split(",") if w.strip()]
        self.parent_view.session.draft["automod"]["blocked_words"] = parsed[:200]
        await interaction.response.defer()
        await self.parent_view.cog.redraw_from_modal(self.parent_view.session)


class EmojisModal(discord.ui.Modal):
    def __init__(self, parent: "SetupStepView", current_emojis: list[str]):
        super().__init__(title="Reaction Role Emojis", timeout=300)
        self.parent_view = parent
        self.emojis = discord.ui.TextInput(
            label="Comma-separated emojis (1-3)",
            style=discord.TextStyle.short,
            required=False,
            max_length=80,
            default=", ".join(current_emojis)[:80],
            placeholder="✅, 🎮, 📣",
        )
        self.add_item(self.emojis)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        parsed = [x.strip() for x in str(self.emojis.value).split(",") if x.strip()]
        self.parent_view.session.draft["reaction_roles"]["emojis"] = parsed[:3]
        await interaction.response.defer()
        await self.parent_view.cog.redraw_from_modal(self.parent_view.session)


class TicketLabelModal(discord.ui.Modal):
    def __init__(self, parent: "SetupStepView", current_text: str, current_label: str):
        super().__init__(title="Ticket Panel Text", timeout=300)
        self.parent_view = parent
        self.message = discord.ui.TextInput(
            label="Ticket message",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=2000,
            default=current_text[:2000],
        )
        self.label = discord.ui.TextInput(
            label="Button label",
            style=discord.TextStyle.short,
            required=True,
            max_length=80,
            default=current_label[:80] or "Open Ticket",
        )
        self.add_item(self.message)
        self.add_item(self.label)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft["tickets"]["ticket_message"] = str(self.message.value).strip()
        self.parent_view.session.draft["tickets"]["button_label"] = str(self.label.value).strip() or "Open Ticket"
        await interaction.response.defer()
        await self.parent_view.cog.redraw_from_modal(self.parent_view.session)


class FeatureSelect(discord.ui.Select):
    def __init__(self, parent: "FeatureSelectView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label=FEATURE_LABELS[k], value=k)
            for k in FEATURE_ORDER
        ]
        super().__init__(
            placeholder="Select modules to configure",
            min_values=1,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft["selected_features"] = list(self.values)
        self.parent_view.session.notice = ""
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class FeatureSelectView(BaseOwnedView):
    def __init__(self, cog: "QuickSetupCog", session: SetupSession) -> None:
        super().__init__(cog, session)
        self.add_item(FeatureSelect(self))

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if not self.session.selected:
            self.session.notice = "Select at least one feature before starting."
            await self.cog.redraw(interaction, self.session)
            return
        self.session.notice = ""
        self.session.step_index = 0
        await self.cog.redraw(interaction, self.session)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.cog.sessions.pop((self.session.guild_id, self.session.user_id), None)
        embed = discord.Embed(
            title="Setup Cancelled",
            description="Draft discarded. No changes were applied.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


class TextChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "SetupStepView", section: str, key: str, placeholder: str) -> None:
        self.parent_view = parent
        self.section = section
        self.key = key
        super().__init__(
            placeholder=placeholder,
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        ch = self.values[0] if self.values else None
        channel_id = getattr(ch, "id", None)
        if isinstance(channel_id, int):
            self.parent_view.session.draft[self.section][self.key] = channel_id
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class CategoryOrChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "SetupStepView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Select ticket category (or channel fallback)",
            channel_types=[discord.ChannelType.category, discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        picked = self.values[0] if self.values else None
        picked_id = getattr(picked, "id", None)
        if isinstance(picked_id, int):
            self.parent_view.session.draft["tickets"]["category_or_channel_id"] = picked_id
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class RoleMultiSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "SetupStepView", section: str, key: str, placeholder: str, max_values: int = 5) -> None:
        self.parent_view = parent
        self.section = section
        self.key = key
        super().__init__(placeholder=placeholder, min_values=1, max_values=max_values)

    async def callback(self, interaction: discord.Interaction) -> None:
        role_ids = [r.id for r in self.values if isinstance(r, discord.Role)]
        self.parent_view.session.draft[self.section][self.key] = role_ids
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class LoggingEventSelect(discord.ui.Select):
    def __init__(self, parent: "SetupStepView") -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Message Delete/Edit", value="message_delete"),
            discord.SelectOption(label="Member Join/Leave", value="member_join"),
            discord.SelectOption(label="Automod Actions", value="automod"),
            discord.SelectOption(label="Warns", value="warn"),
            discord.SelectOption(label="Message Edited", value="message_edit"),
            discord.SelectOption(label="Member Leave", value="member_leave"),
        ]
        super().__init__(
            placeholder="Select logging events",
            min_values=1,
            max_values=min(6, len(options)),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        chosen = set(self.values)
        # Keep this compact: derived pairs are auto-added.
        if "message_delete" in chosen:
            chosen.add("message_edit")
        if "member_join" in chosen:
            chosen.add("member_leave")
        self.parent_view.session.draft["logging"]["events"] = sorted(chosen)
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class MatchTypeSelect(discord.ui.Select):
    def __init__(self, parent: "SetupStepView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Blocked word match type",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Contains", value="contains"),
                discord.SelectOption(label="Starts With", value="starts_with"),
                discord.SelectOption(label="Ends With", value="ends_with"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft["automod"]["match_type"] = self.values[0]
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class SurveyModeSelect(discord.ui.Select):
    def __init__(self, parent: "SetupStepView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Exit survey type",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Preset choices", value="preset"),
                discord.SelectOption(label="Free text", value="free_text"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft["leave"]["survey_mode"] = self.values[0]
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class RRModeSelect(discord.ui.Select):
    def __init__(self, parent: "SetupStepView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Reaction role mode",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Toggle", value="toggle"),
                discord.SelectOption(label="Radio (remove other roles)", value="radio"),
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.draft["reaction_roles"]["mode"] = self.values[0]
        await self.parent_view.cog.redraw(interaction, self.parent_view.session)


class SetupStepView(BaseOwnedView):
    def __init__(self, cog: "QuickSetupCog", session: SetupSession, feature_key: str) -> None:
        super().__init__(cog, session)
        self.feature_key = feature_key
        self._build_feature_controls()
        self._add_nav_buttons()

    def _build_feature_controls(self) -> None:
        if self.feature_key == "welcome":
            self.add_item(TextChannelSelect(self, "welcome", "channel_id", "Welcome channel"))
            self.add_item(_ToggleButton("Toggle Enabled", "welcome", "enabled", discord.ButtonStyle.secondary))
            self.add_item(_ToggleButton("Toggle Embed", "welcome", "embed_enabled", discord.ButtonStyle.secondary))
            self.add_item(_EditMessageButton("Edit Welcome Message", "welcome", "message"))
        elif self.feature_key == "leave":
            self.add_item(TextChannelSelect(self, "leave", "channel_id", "Leave channel"))
            self.add_item(_ToggleButton("Toggle Enabled", "leave", "enabled", discord.ButtonStyle.secondary))
            self.add_item(_ToggleButton("Toggle Embed", "leave", "embed_enabled", discord.ButtonStyle.secondary))
            self.add_item(_ToggleButton("Toggle Exit Survey", "leave", "exit_survey_enabled", discord.ButtonStyle.secondary))
            self.add_item(SurveyModeSelect(self))
            self.add_item(_EditMessageButton("Edit Leave Message", "leave", "message"))
        elif self.feature_key == "logging":
            self.add_item(TextChannelSelect(self, "logging", "channel_id", "Logging channel"))
            self.add_item(_ToggleButton("Toggle Enabled", "logging", "enabled", discord.ButtonStyle.secondary))
            self.add_item(LoggingEventSelect(self))
            self.add_item(_PreviewLoggingButton())
        elif self.feature_key == "automod":
            self.add_item(_ToggleButton("Discord AutoMod", "automod", "use_discord_automod", discord.ButtonStyle.secondary))
            self.add_item(_ToggleButton("Bot-side Checks", "automod", "use_bot_checks", discord.ButtonStyle.secondary))
            self.add_item(MatchTypeSelect(self))
            self.add_item(RoleMultiSelect(self, "automod", "exempt_role_ids", "Exempt roles", max_values=10))
            self.add_item(TextChannelSelect(self, "automod", "exempt_channel_id", "Exempt channel (single quick setup)"))
            self.add_item(_BlockedWordsButton())
        elif self.feature_key == "reaction_roles":
            self.add_item(TextChannelSelect(self, "reaction_roles", "channel_id", "Reaction role panel channel"))
            self.add_item(RRModeSelect(self))
            self.add_item(RoleMultiSelect(self, "reaction_roles", "role_ids", "Select 1-3 roles", max_values=3))
            self.add_item(_ReactionEmojisButton())
            self.add_item(_ReactionMessageButton())
        elif self.feature_key == "tickets":
            self.add_item(_ToggleButton("Enable Tickets", "tickets", "enabled", discord.ButtonStyle.secondary))
            self.add_item(CategoryOrChannelSelect(self))
            self.add_item(RoleMultiSelect(self, "tickets", "support_role_ids", "Support roles", max_values=10))
            self.add_item(TextChannelSelect(self, "tickets", "ticket_channel_id", "Ticket panel channel"))
            self.add_item(_ToggleButton("One Ticket Per User", "tickets", "one_ticket_per_user", discord.ButtonStyle.secondary))
            self.add_item(_TicketTextButton())

    def _add_nav_buttons(self) -> None:
        self.add_item(_BackButton())
        self.add_item(_SkipButton())
        self.add_item(_NextButton())
        self.add_item(_CancelButton())


class ConfirmView(BaseOwnedView):
    def __init__(self, cog: "QuickSetupCog", session: SetupSession) -> None:
        super().__init__(cog, session)
        self.add_item(_BackButton())
        self.add_item(_ConfirmButton())
        self.add_item(_CancelButton())


class _ToggleButton(discord.ui.Button):
    def __init__(self, label: str, section: str, key: str, style: discord.ButtonStyle) -> None:
        super().__init__(label=label, style=style)
        self.section = section
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        current = bool(view.session.draft[self.section].get(self.key, False))
        view.session.draft[self.section][self.key] = not current
        await view.cog.redraw(interaction, view.session)


class _EditMessageButton(discord.ui.Button):
    def __init__(self, label: str, section: str, key: str) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary)
        self.section = section
        self.key = key

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        current = str(view.session.draft[self.section].get(self.key, ""))
        modal = MessageTemplateModal(self.label, view, (self.section, self.key), current)
        await interaction.response.send_modal(modal)


class _BlockedWordsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Edit Blocked Words", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        modal = CommaWordsModal(view, list(view.session.draft["automod"].get("blocked_words", [])))
        await interaction.response.send_modal(modal)


class _ReactionEmojisButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Set Emojis", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        modal = EmojisModal(view, list(view.session.draft["reaction_roles"].get("emojis", [])))
        await interaction.response.send_modal(modal)


class _ReactionMessageButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Edit Panel Message", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        current = str(view.session.draft["reaction_roles"].get("message_text", "Pick your role:"))
        modal = MessageTemplateModal("Reaction Role Message", view, ("reaction_roles", "message_text"), current)
        await interaction.response.send_modal(modal)


class _TicketTextButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Edit Ticket Text", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        data = view.session.draft["tickets"]
        modal = TicketLabelModal(
            view,
            str(data.get("ticket_message", "Click below to create a ticket.")),
            str(data.get("button_label", "Open Ticket")),
        )
        await interaction.response.send_modal(modal)


class _PreviewLoggingButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Test Log (Preview)", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        view.session.notice = "Preview only: a test log entry would be sent after Confirm."
        await view.cog.redraw(interaction, view.session)


class _BackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Back", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BaseOwnedView):
            return
        view.session.notice = ""
        view.session.step_index = max(view.session.step_index - 1, -1)
        await view.cog.redraw(interaction, view.session)


class _SkipButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Skip", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        selected = view.session.selected
        if view.session.step_index < len(selected):
            view.session.step_index += 1
        await view.cog.redraw(interaction, view.session)


class _NextButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Next", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, SetupStepView):
            return
        selected = view.session.selected
        if view.session.step_index < len(selected):
            view.session.step_index += 1
        await view.cog.redraw(interaction, view.session)


class _CancelButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Cancel", style=discord.ButtonStyle.danger)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, BaseOwnedView):
            return
        view.cog.sessions.pop((view.session.guild_id, view.session.user_id), None)
        embed = discord.Embed(
            title="Setup Cancelled",
            description="Draft discarded. No changes were applied.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, view=None)


class _ConfirmButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Confirm", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ConfirmView):
            return
        await view.cog.confirm(interaction, view.session)


class QuickSetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.sessions: dict[tuple[int, int], SetupSession] = {}

    def _session_key(self, guild_id: int, user_id: int) -> tuple[int, int]:
        return (guild_id, user_id)

    async def redraw_from_modal(self, session: SetupSession) -> None:
        if session.message is None:
            return
        embed, view = self.build_screen(session)
        await session.message.edit(embed=embed, view=view)

    async def redraw(self, interaction: discord.Interaction, session: SetupSession) -> None:
        embed, view = self.build_screen(session)
        await interaction.response.edit_message(embed=embed, view=view)
        if session.message is None:
            try:
                session.message = await interaction.original_response()
            except Exception:
                session.message = None

    def build_screen(self, session: SetupSession) -> tuple[discord.Embed, discord.ui.View]:
        if session.step_index < 0:
            embed = discord.Embed(
                title="Coffeecord Quick Setup",
                description="Select what you want to configure. You can skip anything.",
                color=discord.Color.blurple(),
            )
            selected = session.selected
            embed.add_field(
                name="Selected",
                value=", ".join(FEATURE_LABELS[x] for x in selected) if selected else "Nothing selected yet.",
                inline=False,
            )
            if session.notice:
                embed.add_field(name="Notice", value=session.notice, inline=False)
            return embed, FeatureSelectView(self, session)

        selected = session.selected
        if session.step_index >= len(selected):
            return self._build_confirm_screen(session), ConfirmView(self, session)

        feature_key = selected[session.step_index]
        embed = self._build_step_embed(session, feature_key)
        return embed, SetupStepView(self, session, feature_key)

    def _build_confirm_screen(self, session: SetupSession) -> discord.Embed:
        embed = discord.Embed(
            title="Confirm Setup",
            description="Review your draft. Nothing has been applied yet.",
            color=discord.Color.gold(),
        )
        for key in session.selected:
            summary = self._module_summary(session.draft, key)
            embed.add_field(name=FEATURE_LABELS[key], value=summary, inline=False)
        if session.notice:
            embed.add_field(name="Notice", value=session.notice, inline=False)
        return embed

    def _build_step_embed(self, session: SetupSession, feature_key: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"Quick Setup • {FEATURE_LABELS[feature_key]}",
            color=discord.Color.blurple(),
            description="This is a draft step. Nothing is applied until Confirm.",
        )
        embed.add_field(
            name="Current Draft",
            value=self._module_summary(session.draft, feature_key),
            inline=False,
        )
        embed.set_footer(text=f"Step {session.step_index + 1}/{max(len(session.selected), 1)}")
        if session.notice:
            embed.add_field(name="Notice", value=session.notice, inline=False)
        return embed

    def _module_summary(self, draft: dict[str, Any], key: str) -> str:
        if key == "welcome":
            d = draft["welcome"]
            return (
                f"enabled={d['enabled']}, embed={d['embed_enabled']}, "
                f"channel_id={d['channel_id']}, template={d['message'][:80]}"
            )
        if key == "leave":
            d = draft["leave"]
            return (
                f"enabled={d['enabled']}, embed={d['embed_enabled']}, exit_survey={d['exit_survey_enabled']} "
                f"({d.get('survey_mode', 'preset')}), channel_id={d['channel_id']}"
            )
        if key == "logging":
            d = draft["logging"]
            return f"enabled={d['enabled']}, channel_id={d['channel_id']}, events={', '.join(d['events']) or 'none'}"
        if key == "automod":
            d = draft["automod"]
            return (
                f"use_discord_automod={d['use_discord_automod']}, use_bot_checks={d['use_bot_checks']}, "
                f"match={d['match_type']}, blocked_words={len(d['blocked_words'])}"
            )
        if key == "reaction_roles":
            d = draft["reaction_roles"]
            return (
                f"enabled={d['enabled']}, channel_id={d['channel_id']}, mode={d['mode']}, "
                f"roles={len(d['role_ids'])}, emojis={len(d['emojis'])}"
            )
        if key == "tickets":
            d = draft["tickets"]
            return (
                f"enabled={d['enabled']}, panel_channel_id={d['ticket_channel_id']}, "
                f"support_roles={len(d['support_role_ids'])}, one_ticket_per_user={d['one_ticket_per_user']}"
            )
        return "Not configured."

    @app_commands.command(name="setup", description="Start Coffeecord quick setup wizard.")
    async def setup_command(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        session = SetupSession(guild_id=interaction.guild.id, user_id=interaction.user.id)
        self.sessions[self._session_key(session.guild_id, session.user_id)] = session
        embed, view = self.build_screen(session)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            session.message = await interaction.original_response()
        except Exception:
            session.message = None

    @app_commands.command(name="setup_resume", description="Resume your quick setup draft.")
    async def setup_resume(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        session = self.sessions.get(self._session_key(interaction.guild.id, interaction.user.id))
        if session is None:
            await interaction.response.send_message("No active draft found. Use `/setup` first.", ephemeral=True)
            return
        embed, view = self.build_screen(session)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        try:
            session.message = await interaction.original_response()
        except Exception:
            session.message = None

    @app_commands.command(name="setup_cancel", description="Discard your quick setup draft.")
    async def setup_cancel(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        key = self._session_key(interaction.guild.id, interaction.user.id)
        if key in self.sessions:
            self.sessions.pop(key, None)
            await interaction.response.send_message("Draft discarded.", ephemeral=True)
            return
        await interaction.response.send_message("No active draft found.", ephemeral=True)

    async def confirm(self, interaction: discord.Interaction, session: SetupSession) -> None:
        if interaction.guild is None:
            await interaction.response.send_message("Run this in a server.", ephemeral=True)
            return
        errors = self._validate_before_confirm(interaction.guild, session.draft)
        if errors:
            session.notice = "Fix permissions/config before confirming."
            embed = discord.Embed(
                title="Cannot Apply Setup",
                description="\n".join(f"• {e}" for e in errors[:20]),
                color=discord.Color.red(),
            )
            await interaction.response.edit_message(embed=embed, view=ConfirmView(self, session))
            return

        await interaction.response.defer()
        ok, message = await self._apply_draft(interaction.guild, session.draft)
        if ok:
            self.sessions.pop(self._session_key(session.guild_id, session.user_id), None)
            embed = discord.Embed(
                title="Setup Applied",
                description="All selected modules were saved successfully.",
                color=discord.Color.green(),
            )
            await interaction.edit_original_response(embed=embed, view=None)
            return

        embed = discord.Embed(
            title="Apply Failed",
            description=message,
            color=discord.Color.red(),
        )
        await interaction.edit_original_response(embed=embed, view=ConfirmView(self, session))

    def _validate_before_confirm(self, guild: discord.Guild, draft: dict[str, Any]) -> list[str]:
        issues: list[str] = []
        me = guild.me
        if me is None:
            issues.append("Bot member object is unavailable in this guild.")
            return issues

        def _check_text_channel(channel_id: Optional[int], label: str, need_embed: bool = False) -> None:
            if not channel_id:
                issues.append(f"{label}: channel is not selected.")
                return
            channel = guild.get_channel(int(channel_id))
            if not isinstance(channel, discord.TextChannel):
                issues.append(f"{label}: selected channel is invalid.")
                return
            perms = channel.permissions_for(me)
            if not perms.view_channel or not perms.send_messages:
                issues.append(f"{label}: bot needs view/send messages in {channel.mention}.")
            if need_embed and not perms.embed_links:
                issues.append(f"{label}: bot needs embed links in {channel.mention}.")

        if "welcome" in draft["selected_features"]:
            w = draft["welcome"]
            _check_text_channel(w.get("channel_id"), "Welcome", need_embed=bool(w.get("embed_enabled", False)))
        if "leave" in draft["selected_features"]:
            l = draft["leave"]
            _check_text_channel(l.get("channel_id"), "Leave", need_embed=bool(l.get("embed_enabled", False)))
        if "logging" in draft["selected_features"]:
            lg = draft["logging"]
            _check_text_channel(lg.get("channel_id"), "Logging", need_embed=True)
        if "reaction_roles" in draft["selected_features"]:
            rr = draft["reaction_roles"]
            if rr.get("enabled", True):
                _check_text_channel(rr.get("channel_id"), "Reaction Roles", need_embed=False)
                roles = rr.get("role_ids", [])
                if not roles:
                    issues.append("Reaction Roles: select at least one role.")
        if "tickets" in draft["selected_features"]:
            tk = draft["tickets"]
            if tk.get("enabled", True):
                _check_text_channel(tk.get("ticket_channel_id"), "Tickets", need_embed=True)
                if not tk.get("support_role_ids"):
                    issues.append("Tickets: select at least one support role.")
                if not me.guild_permissions.manage_channels:
                    issues.append("Tickets: bot needs Manage Channels to create ticket channels.")
                if not me.guild_permissions.manage_roles:
                    issues.append("Tickets: bot needs Manage Roles to apply ticket overwrites.")
        if "automod" in draft["selected_features"]:
            a = draft["automod"]
            if a.get("use_discord_automod", False) and not me.guild_permissions.manage_guild:
                issues.append("Automod: bot needs Manage Guild for Discord AutoMod rule management.")
        return issues

    async def _apply_draft(self, guild: discord.Guild, draft: dict[str, Any]) -> tuple[bool, str]:
        selected = set(draft.get("selected_features", []))
        gid = str(guild.id)
        backups: dict[Path, str] = {}
        created_messages: list[discord.Message] = []

        paths = [WELCOME_LEAVE_PATH, LOGGING_PATH, AUTOMOD_PATH, REACTIONROLE_PATH, TICKETS_PATH]
        for path in paths:
            if path.exists():
                try:
                    backups[path] = path.read_text(encoding="utf-8")
                except OSError:
                    backups[path] = ""
            else:
                backups[path] = ""

        try:
            if "welcome" in selected or "leave" in selected:
                root = _read_json(WELCOME_LEAVE_PATH, {})
                cfg = root.get(gid, {})
                if not isinstance(cfg, dict):
                    cfg = {}
                if "welcome" in selected:
                    w = draft["welcome"]
                    prev_w = cfg.get("welcome")
                    if not isinstance(prev_w, dict):
                        prev_w = {}
                    cfg["welcome"] = normalize_welcome_section(
                        {
                            **prev_w,
                            "enabled": bool(w["enabled"]),
                            "channel_id": int(w["channel_id"]) if w.get("channel_id") else None,
                            "message": str(w["message"]),
                            "embed_enabled": bool(w["embed_enabled"]),
                        }
                    )
                if "leave" in selected:
                    l = draft["leave"]
                    existing_leave = cfg.get("leave", {})
                    if not isinstance(existing_leave, dict):
                        existing_leave = {}
                    existing_survey_log_channel_id = existing_leave.get("exit_survey_log_channel_id")
                    if not isinstance(existing_survey_log_channel_id, int):
                        existing_survey_log_channel_id = None
                    survey_log_channel_id = l.get(
                        "exit_survey_log_channel_id",
                        existing_survey_log_channel_id,
                    )
                    cfg["leave"] = {
                        "enabled": bool(l["enabled"]),
                        "channel_id": int(l["channel_id"]) if l.get("channel_id") else None,
                        "message": str(l["message"]),
                        "embed_enabled": bool(l["embed_enabled"]),
                        "exit_survey_enabled": bool(l["exit_survey_enabled"]),
                        "exit_survey_log_channel_id": int(survey_log_channel_id) if survey_log_channel_id else None,
                        "survey_mode": str(l.get("survey_mode", "preset")),
                    }
                root[gid] = cfg
                _write_json(WELCOME_LEAVE_PATH, root)

            if "logging" in selected:
                root = _read_json(LOGGING_PATH, {})
                cfg = root.get(gid, {})
                if not isinstance(cfg, dict):
                    cfg = {}
                lg = draft["logging"]
                selected_events = set(lg.get("events", []))
                event_map = {}
                for key in [
                    "message_delete",
                    "message_edit",
                    "member_join",
                    "member_leave",
                    "timeout",
                    "ban",
                    "unban",
                    "warn",
                    "automod",
                    "ticket_event",
                    "command_use",
                    "role_create",
                    "role_delete",
                    "role_update",
                    "channel_create",
                    "channel_delete",
                    "channel_update",
                    "voice_join",
                    "voice_leave",
                    "voice_move",
                    "nickname_change",
                    "role_assign",
                    "role_remove",
                ]:
                    event_map[key] = key in selected_events
                cfg["enabled"] = bool(lg.get("enabled", True))
                cfg["log_channel_id"] = int(lg["channel_id"]) if lg.get("channel_id") else None
                cfg["events"] = event_map
                cfg.setdefault("modules", {})
                root[gid] = cfg
                _write_json(LOGGING_PATH, root)

            if "automod" in selected:
                root = _read_json(AUTOMOD_PATH, {"default": {}})
                a = draft["automod"]
                guild_cfg = root.get(gid, {})
                if not isinstance(guild_cfg, dict):
                    guild_cfg = {}
                guild_cfg["enabled"] = bool(a.get("use_bot_checks", True))
                guild_cfg.setdefault("bad_words", {})
                guild_cfg["bad_words"]["enabled"] = bool(a.get("use_bot_checks", True))
                guild_cfg["bad_words"]["words"] = list(a.get("blocked_words", []))
                guild_cfg["bad_words"]["action"] = "warn"
                guild_cfg["bad_words"]["delete_message"] = True
                guild_cfg["bad_words"]["match_type"] = str(a.get("match_type", "contains"))
                guild_cfg.setdefault("whitelist", {"roles": [], "channels": []})
                guild_cfg["whitelist"]["roles"] = list(a.get("exempt_role_ids", []))
                exempt_channel_id = a.get("exempt_channel_id")
                guild_cfg["whitelist"]["channels"] = [int(exempt_channel_id)] if exempt_channel_id else []
                guild_cfg["hybrid"] = {
                    "use_discord_automod": bool(a.get("use_discord_automod", True)),
                    "use_bot_checks": bool(a.get("use_bot_checks", True)),
                }
                root[gid] = guild_cfg
                _write_json(AUTOMOD_PATH, root)

            if "reaction_roles" in selected:
                rr = draft["reaction_roles"]
                root = _read_json(REACTIONROLE_PATH, {})
                guild_cfg = root.get(gid, {})
                if not isinstance(guild_cfg, dict):
                    guild_cfg = {}
                guild_cfg["enabled"] = bool(rr.get("enabled", True))
                guild_cfg["default_mode"] = "button"
                guild_cfg["default_logging"] = True
                guild_cfg.setdefault("messages", {})
                root[gid] = guild_cfg
                _write_json(REACTIONROLE_PATH, root)

                channel_id = rr.get("channel_id")
                role_ids = list(rr.get("role_ids", []))
                if rr.get("enabled", True) and channel_id and role_ids:
                    channel = guild.get_channel(int(channel_id))
                    if isinstance(channel, discord.TextChannel):
                        message_text = str(rr.get("message_text", "Pick your role:")).strip() or "Pick your role:"
                        mappings = []
                        emojis = list(rr.get("emojis", []))
                        for idx, rid in enumerate(role_ids[:3]):
                            emoji = emojis[idx] if idx < len(emojis) else None
                            mappings.append(
                                {
                                    "id": f"map_qs_{idx+1}",
                                    "role_id": int(rid),
                                    "label": f"Role {idx + 1}",
                                    "emoji": str(emoji) if emoji else None,
                                }
                            )
                        panel_embed = _build_panel_embed(
                            message_text,
                            "",
                            "",
                            DEFAULT_PANEL_COLOR,
                            mappings,
                            guild,
                        )
                        panel = await channel.send(embed=panel_embed)
                        created_messages.append(panel)
                        guild_cfg["messages"][str(panel.id)] = {
                            "channel_id": int(channel.id),
                            "mode": "button",
                            "content": message_text,
                            "embed": {"title": "", "description": "", "color": DEFAULT_PANEL_COLOR},
                            "mappings": mappings,
                            "max_roles": 1 if rr.get("mode") == "radio" else 0,
                            "required_role_ids": [],
                            "remove_others": rr.get("mode") == "radio",
                            "logging": True,
                        }
                        root[gid] = guild_cfg
                        _write_json(REACTIONROLE_PATH, root)

            if "tickets" in selected:
                tk = draft["tickets"]
                root = _read_json(TICKETS_PATH, {})
                if not tk.get("enabled", True):
                    root.pop(gid, None)
                    _write_json(TICKETS_PATH, root)
                else:
                    panel_channel = guild.get_channel(int(tk["ticket_channel_id"])) if tk.get("ticket_channel_id") else None
                    if not isinstance(panel_channel, discord.TextChannel):
                        raise RuntimeError("Tickets panel channel is invalid.")
                    support_roles = [int(x) for x in tk.get("support_role_ids", []) if str(x).isdigit()]
                    if not support_roles:
                        raise RuntimeError("Tickets support roles are missing.")
                    root[gid] = {
                        "ticket_channel": int(panel_channel.id),
                        "support_roles": support_roles,
                        "ticket_types": ["Support"],
                        "ticket_message": str(tk.get("ticket_message", "Click below to create a ticket.")),
                        "tickets": {},
                        "button_label": str(tk.get("button_label", "Open Ticket")),
                        "category_id": int(tk["category_or_channel_id"]) if tk.get("category_or_channel_id") else None,
                        "one_ticket_per_user": bool(tk.get("one_ticket_per_user", True)),
                    }
                    _write_json(TICKETS_PATH, root)

                    # Create panel only after config write and only on confirm.
                    try:
                        import tickets  # type: ignore
                        embed = discord.Embed(
                            title="🎫 Support Tickets",
                            description=str(tk.get("ticket_message", "Click below to create a ticket.")),
                            color=discord.Color.blue(),
                        )
                        panel = await panel_channel.send(embed=embed, view=tickets.TicketPanel(gid))
                        created_messages.append(panel)
                    except Exception as exc:
                        raise RuntimeError(f"Ticket panel post failed: {exc}") from exc

            return True, "ok"
        except Exception as exc:
            # Roll back files and clean up created messages if anything fails.
            for msg in created_messages:
                try:
                    await msg.delete()
                except Exception:
                    pass
            for path, content in backups.items():
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    if content:
                        path.write_text(content, encoding="utf-8")
                    elif path.exists():
                        path.unlink()
                except Exception:
                    pass
            return False, f"No changes applied. Reason: {exc}"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuickSetupCog(bot))
