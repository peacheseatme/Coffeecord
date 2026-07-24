import asyncio
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Optional

import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t_sync

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "Storage" / "Config" / "autorole_config.json"

LOGGER = logging.getLogger("coffeecord.autorole")
_CONFIG_LOCK = asyncio.Lock()

DEFAULT_CONDITIONS = {
    "min_account_age_days": 0,
    "require_roles": [],
    "exclude_roles": [],
    "ignore_bots": True,
    "require_not_timed_out": False,
}

VALID_EVENTS = {"member_join", "first_message", "verified", "reaction_add", "level_up"}
AUTOROLE_I18N_PREFIX = "autorole."


def _autorole_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{AUTOROLE_I18N_PREFIX}{key}", default=default, **params)


def _read_config_sync() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CONFIG_PATH.open("w", encoding="utf-8") as fp:
            json.dump({}, fp, indent=2, ensure_ascii=True)
        return {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fp:
            raw = json.load(fp)
        if isinstance(raw, dict):
            return raw
        return {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_config_sync(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_PATH.open("w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=True)


def _normalize_conditions(raw: Any) -> dict[str, Any]:
    data = dict(DEFAULT_CONDITIONS)
    if not isinstance(raw, dict):
        return data
    data["min_account_age_days"] = max(int(raw.get("min_account_age_days", 0) or 0), 0)
    req = raw.get("require_roles", [])
    ex = raw.get("exclude_roles", [])
    data["require_roles"] = [int(x) for x in req if str(x).isdigit()] if isinstance(req, list) else []
    data["exclude_roles"] = [int(x) for x in ex if str(x).isdigit()] if isinstance(ex, list) else []
    data["ignore_bots"] = bool(raw.get("ignore_bots", True))
    data["require_not_timed_out"] = bool(raw.get("require_not_timed_out", False))
    return data


def _normalize_rule(raw: Any) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    event = str(raw.get("event", "")).strip()
    if event not in VALID_EVENTS:
        return None
    roles_raw = raw.get("roles", [])
    roles = [int(r) for r in roles_raw if str(r).isdigit()] if isinstance(roles_raw, list) else []
    if not roles:
        return None
    delay_seconds = max(int(raw.get("delay_seconds", 0) or 0), 0)
    rule_id = str(raw.get("id") or f"rule_{uuid.uuid4().hex[:8]}")
    return {
        "id": rule_id,
        "event": event,
        "roles": roles,
        "delay_seconds": delay_seconds,
        "conditions": _normalize_conditions(raw.get("conditions", {})),
    }


def _default_guild_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "rules": [],
        "pending": [],
        "first_message_users": [],
    }


def _normalize_pending(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not str(item.get("member_id", "")).isdigit():
            continue
        if not str(item.get("rule_id", "")):
            continue
        due_at = int(item.get("due_at", 0) or 0)
        if due_at <= 0:
            continue
        out.append(
            {
                "pending_id": str(item.get("pending_id") or uuid.uuid4().hex),
                "member_id": int(item["member_id"]),
                "rule_id": str(item["rule_id"]),
                "event": str(item.get("event", "")),
                "due_at": due_at,
            }
        )
    return out


def _normalize_guild_config(raw: Any) -> dict[str, Any]:
    cfg = _default_guild_config()
    if not isinstance(raw, dict):
        return cfg
    cfg["enabled"] = bool(raw.get("enabled", True))
    rules_raw = raw.get("rules", [])
    if isinstance(rules_raw, list):
        for entry in rules_raw:
            normalized = _normalize_rule(entry)
            if normalized is not None:
                cfg["rules"].append(normalized)
    cfg["pending"] = _normalize_pending(raw.get("pending", []))
    seen = raw.get("first_message_users", [])
    if isinstance(seen, list):
        cfg["first_message_users"] = [int(x) for x in seen if str(x).isdigit()]
    return cfg


class _AutoRoleSetupView(discord.ui.View):
    def __init__(
        self,
        cog: "AutoRoleCog",
        invoker_id: int,
        event_default: Optional[str],
        delay_seconds: int,
        conditions: dict[str, Any],
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.invoker_id = invoker_id
        self.delay_seconds = delay_seconds
        self.conditions = conditions
        self.selected_event = event_default if event_default in VALID_EVENTS else "member_join"
        self.selected_roles: list[discord.Role] = []
        self.add_item(_EventSelect(self))
        self.add_item(_RolePicker(self))
        self.add_item(_SaveButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.invoker_id:
            await interaction.response.send_message(
                _autorole_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "setup_panel_owner_only",
                    default="This setup panel belongs to someone else.",
                ),
                ephemeral=True,
            )
            return False
        return True


class _EventSelect(discord.ui.Select):
    def __init__(self, parent: _AutoRoleSetupView) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(label="Member Join", value="member_join"),
            discord.SelectOption(label="First Message", value="first_message"),
            discord.SelectOption(label="Verified", value="verified"),
            discord.SelectOption(label="Reaction Added", value="reaction_add"),
            discord.SelectOption(label="Level Up", value="level_up"),
        ]
        super().__init__(placeholder="Select trigger event", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_event = self.values[0]
        await interaction.response.send_message(
            _autorole_text_sync(
                interaction.guild.id if interaction.guild else None,
                "event_set",
                default="Event set to `{event}`.",
                event=self.values[0],
            ),
            ephemeral=True,
        )


class _RolePicker(discord.ui.RoleSelect):
    def __init__(self, parent: _AutoRoleSetupView) -> None:
        self.parent_view = parent
        super().__init__(placeholder="Select one or more roles", min_values=1, max_values=10)

    async def callback(self, interaction: discord.Interaction) -> None:
        self.parent_view.selected_roles = [r for r in self.values if isinstance(r, discord.Role)]
        role_text = ", ".join(r.mention for r in self.parent_view.selected_roles)
        await interaction.response.send_message(
            _autorole_text_sync(
                interaction.guild.id if interaction.guild else None,
                "roles_selected",
                default="Roles selected: {roles}",
                roles=role_text,
            ),
            ephemeral=True,
        )


class _SaveButton(discord.ui.Button):
    def __init__(self, parent: _AutoRoleSetupView) -> None:
        self.parent_view = parent
        super().__init__(label="Save Rule", style=discord.ButtonStyle.success)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.parent_view.selected_roles:
            await interaction.response.send_message(
                _autorole_text_sync(
                    interaction.guild.id if interaction.guild else None,
                    "pick_one_role",
                    default="Pick at least one role first.",
                ),
                ephemeral=True,
            )
            return
        role_ids = [r.id for r in self.parent_view.selected_roles]
        cfg = await self.parent_view.cog.load_autorole_config(interaction.guild.id)
        rule = {
            "id": f"rule_{uuid.uuid4().hex[:8]}",
            "event": self.parent_view.selected_event,
            "roles": role_ids,
            "delay_seconds": self.parent_view.delay_seconds,
            "conditions": _normalize_conditions(self.parent_view.conditions),
        }
        cfg["rules"].append(rule)
        await self.parent_view.cog.save_autorole_config(interaction.guild.id, cfg)
        gid = interaction.guild.id if interaction.guild else None
        embed = discord.Embed(
            title=_autorole_text_sync(gid, "rule_added_title", default="Auto Role Rule Added"),
            color=discord.Color.green(),
        )
        embed.add_field(
            name=_autorole_text_sync(gid, "field_rule_id", default="Rule ID"),
            value=rule["id"],
            inline=False,
        )
        embed.add_field(
            name=_autorole_text_sync(gid, "field_event", default="Event"),
            value=rule["event"],
            inline=True,
        )
        embed.add_field(
            name=_autorole_text_sync(gid, "field_delay", default="Delay"),
            value=f"{rule['delay_seconds']}s",
            inline=True,
        )
        embed.add_field(
            name=_autorole_text_sync(gid, "field_roles", default="Roles"),
            value=", ".join(f"<@&{rid}>" for rid in rule["roles"])[:1024],
            inline=False,
        )
        await interaction.response.edit_message(
            content=_autorole_text_sync(gid, "rule_saved", default="✅ Rule saved."),
            embed=embed,
            view=None,
        )
        self.parent_view.stop()


class AutoRoleCog(
    commands.GroupCog,
    group_name="autorole",
    group_description="Configure and test automatic role rules.",
):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._config: dict[str, Any] = {}
        self._pending_tasks: dict[tuple[int, int, str], asyncio.Task] = {}

    async def cog_load(self) -> None:
        await self._reload_config()
        await self._resume_pending_tasks()

    async def cog_unload(self) -> None:
        for task in self._pending_tasks.values():
            task.cancel()
        self._pending_tasks.clear()

    async def _reload_config(self) -> None:
        async with _CONFIG_LOCK:
            raw = await asyncio.to_thread(_read_config_sync)
            normalized: dict[str, Any] = {}
            for guild_id, guild_cfg in raw.items():
                if str(guild_id).isdigit():
                    normalized[str(guild_id)] = _normalize_guild_config(guild_cfg)
            self._config = normalized
            await asyncio.to_thread(_write_config_sync, self._config)

    async def load_autorole_config(self, guild_id: int) -> dict[str, Any]:
        key = str(guild_id)
        cfg = self._config.get(key)
        if cfg is None:
            cfg = _default_guild_config()
            self._config[key] = cfg
            await self.save_autorole_config(guild_id, cfg)
        return _normalize_guild_config(cfg)

    async def save_autorole_config(self, guild_id: int, data: dict[str, Any]) -> None:
        async with _CONFIG_LOCK:
            self._config[str(guild_id)] = _normalize_guild_config(data)
            await asyncio.to_thread(_write_config_sync, self._config)

    def check_conditions(self, member: discord.Member, conditions: dict[str, Any]) -> tuple[bool, list[str]]:
        c = _normalize_conditions(conditions)
        failed: list[str] = []
        if c["ignore_bots"] and member.bot:
            failed.append("member is a bot")
        if c["min_account_age_days"] > 0:
            age_days = (discord.utils.utcnow() - member.created_at).days
            if age_days < c["min_account_age_days"]:
                failed.append(f"account age {age_days}d < required {c['min_account_age_days']}d")
        if c["require_not_timed_out"] and member.is_timed_out():
            failed.append("member is timed out")
        if c["require_roles"]:
            current = {r.id for r in member.roles}
            missing = [rid for rid in c["require_roles"] if rid not in current]
            if missing:
                failed.append(f"missing required roles: {', '.join(str(x) for x in missing)}")
        if c["exclude_roles"]:
            current = {r.id for r in member.roles}
            blocked = [rid for rid in c["exclude_roles"] if rid in current]
            if blocked:
                failed.append(f"has excluded roles: {', '.join(str(x) for x in blocked)}")
        return len(failed) == 0, failed

    async def _apply_rule_roles(self, guild: discord.Guild, member: discord.Member, rule: dict[str, Any]) -> tuple[list[int], list[int]]:
        applied: list[int] = []
        already: list[int] = []
        me = guild.me
        for role_id in rule["roles"]:
            role = guild.get_role(int(role_id))
            if role is None:
                continue
            if role in member.roles:
                already.append(role.id)
                continue
            if me is None or me.top_role <= role:
                LOGGER.warning("Cannot assign role %s in guild %s due to hierarchy.", role.id, guild.id)
                continue
            try:
                await member.add_roles(role, reason=f"AutoRole rule {rule['id']} ({rule['event']})")
                applied.append(role.id)
            except discord.Forbidden:
                LOGGER.warning("Missing permission to assign role %s in guild %s", role.id, guild.id)
                if guild.system_channel and me and guild.system_channel.permissions_for(me).send_messages:
                    try:
                        await guild.system_channel.send(
                            f"⚠️ Auto Roles couldn't assign {role.mention} due to missing permissions."
                        )
                    except discord.HTTPException:
                        pass
            except discord.HTTPException:
                LOGGER.warning("Failed assigning role %s to member %s", role.id, member.id)
        return applied, already

    async def _process_event_for_member(self, guild: discord.Guild, member: discord.Member, event_name: str) -> list[str]:
        cfg = await self.load_autorole_config(guild.id)
        if not cfg.get("enabled", True):
            return []
        matched: list[str] = []
        for rule in cfg["rules"]:
            if rule["event"] != event_name:
                continue
            ok, failures = self.check_conditions(member, rule["conditions"])
            if not ok:
                continue
            delay = int(rule.get("delay_seconds", 0) or 0)
            if delay > 0:
                await self._enqueue_delayed_assignment(guild.id, member.id, rule["id"], event_name, delay)
                matched.append(f"{rule['id']} (scheduled in {delay}s)")
                continue
            applied, already = await self._apply_rule_roles(guild, member, rule)
            if applied or already:
                matched.append(rule["id"])
        return matched

    async def _enqueue_delayed_assignment(
        self,
        guild_id: int,
        member_id: int,
        rule_id: str,
        event_name: str,
        delay_seconds: int,
    ) -> None:
        due_at = int(time.time()) + max(delay_seconds, 0)
        cfg = await self.load_autorole_config(guild_id)
        pending = cfg.setdefault("pending", [])
        pending_id = uuid.uuid4().hex
        pending.append(
            {
                "pending_id": pending_id,
                "member_id": member_id,
                "rule_id": rule_id,
                "event": event_name,
                "due_at": due_at,
            }
        )
        await self.save_autorole_config(guild_id, cfg)
        self._schedule_pending_task(guild_id, member_id, rule_id, pending_id, due_at)

    def _schedule_pending_task(self, guild_id: int, member_id: int, rule_id: str, pending_id: str, due_at: int) -> None:
        key = (guild_id, member_id, pending_id)
        if key in self._pending_tasks:
            return
        self._pending_tasks[key] = asyncio.create_task(
            self._run_delayed_assignment(guild_id, member_id, rule_id, pending_id, due_at)
        )

    async def _run_delayed_assignment(self, guild_id: int, member_id: int, rule_id: str, pending_id: str, due_at: int) -> None:
        key = (guild_id, member_id, pending_id)
        try:
            wait_for = max(due_at - int(time.time()), 0)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                return
            member = guild.get_member(member_id)
            if member is None:
                return
            cfg = await self.load_autorole_config(guild_id)
            rule = next((r for r in cfg["rules"] if r["id"] == rule_id), None)
            if rule is None:
                return
            ok, _ = self.check_conditions(member, rule["conditions"])
            if not ok:
                return
            await self._apply_rule_roles(guild, member, rule)
        finally:
            cfg = await self.load_autorole_config(guild_id)
            cfg["pending"] = [p for p in cfg.get("pending", []) if p.get("pending_id") != pending_id]
            await self.save_autorole_config(guild_id, cfg)
            self._pending_tasks.pop(key, None)

    async def _resume_pending_tasks(self) -> None:
        for guild_id_str, guild_cfg in list(self._config.items()):
            if not str(guild_id_str).isdigit():
                continue
            guild_id = int(guild_id_str)
            normalized = _normalize_guild_config(guild_cfg)
            for pending in normalized.get("pending", []):
                self._schedule_pending_task(
                    guild_id,
                    int(pending["member_id"]),
                    str(pending["rule_id"]),
                    str(pending["pending_id"]),
                    int(pending["due_at"]),
                )

    @app_commands.command(
        name="status",
        description="Show current auto role configuration.",
)
    async def autorole_status(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_autorole_config(interaction.guild.id)
        rules = cfg.get("rules", [])
        embed = discord.Embed(
            title=_autorole_text_sync(interaction.user.id, "status_title", default="Auto Roles Status"),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name=_autorole_text_sync(interaction.user.id, "field_enabled", default="Enabled"),
            value=t_sync(interaction.user.id, "common.yes") if cfg.get("enabled", True) else t_sync(interaction.user.id, "common.no"),
            inline=True,
        )
        embed.add_field(
            name=_autorole_text_sync(interaction.user.id, "field_rule_count", default="Rule Count"),
            value=str(len(rules)),
            inline=True,
        )
        lines = []
        for rule in rules[:10]:
            roles = ", ".join(f"<@&{rid}>" for rid in rule["roles"])
            lines.append(f"`{rule['id']}` • `{rule['event']}` -> {roles}")
        embed.add_field(
            name=_autorole_text_sync(interaction.user.id, "field_rules", default="Rules"),
            value="\n".join(lines)
            if lines
            else _autorole_text_sync(interaction.user.id, "no_rules", default="No rules configured."),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="toggle",
        description="Enable or disable auto roles for this server.",
)
    async def autorole_toggle(self, interaction: discord.Interaction, enabled: Optional[bool] = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_autorole_config(interaction.guild.id)
        cfg["enabled"] = (not bool(cfg.get("enabled", True))) if enabled is None else bool(enabled)
        await self.save_autorole_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _autorole_text_sync(
                interaction.guild.id,
                "toggle_result",
                default="Auto roles are now **{state}**.",
                state="enabled" if cfg["enabled"] else "disabled",
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="add",
        description="Create an auto role rule with a simple interactive setup.",
)
    @app_commands.describe(
        event="When this rule should run.",
        delay_seconds="Optional delay before assigning roles.",
        min_account_age_days="Optional minimum account age.",
        require_role="Optional role the member must have.",
        exclude_role="Optional role the member must NOT have.",
        ignore_bots="Ignore bot accounts.",
        require_not_timed_out="Require member to not be timed out.",
    )
    @app_commands.choices(
        event=[
            app_commands.Choice(name="Member Join", value="member_join"),
            app_commands.Choice(name="First Message", value="first_message"),
            app_commands.Choice(name="Verified", value="verified"),
            app_commands.Choice(name="Reaction Added", value="reaction_add"),
            app_commands.Choice(name="Level Up", value="level_up"),
        ]
    )
    async def autorole_add(
        self,
        interaction: discord.Interaction,
        event: Optional[app_commands.Choice[str]] = None,
        delay_seconds: int = 0,
        min_account_age_days: int = 0,
        require_role: Optional[discord.Role] = None,
        exclude_role: Optional[discord.Role] = None,
        ignore_bots: bool = True,
        require_not_timed_out: bool = False,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        conditions = {
            "min_account_age_days": max(min_account_age_days, 0),
            "require_roles": [require_role.id] if require_role else [],
            "exclude_roles": [exclude_role.id] if exclude_role else [],
            "ignore_bots": ignore_bots,
            "require_not_timed_out": require_not_timed_out,
        }
        view = _AutoRoleSetupView(
            cog=self,
            invoker_id=interaction.user.id,
            event_default=(event.value if event else "member_join"),
            delay_seconds=max(delay_seconds, 0),
            conditions=conditions,
        )
        await interaction.response.send_message(
            _autorole_text_sync(
                interaction.guild.id,
                "add_prompt",
                default="Select an event and role(s), then press **Save Rule**.",
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="remove",
        description="Remove an auto role rule by ID.",
)
    async def autorole_remove(self, interaction: discord.Interaction, rule_id: str) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_autorole_config(interaction.guild.id)
        before = len(cfg["rules"])
        cfg["rules"] = [r for r in cfg["rules"] if r["id"] != rule_id]
        cfg["pending"] = [p for p in cfg.get("pending", []) if p.get("rule_id") != rule_id]
        if len(cfg["rules"]) == before:
            await interaction.response.send_message(
                _autorole_text_sync(interaction.user.id, "rule_not_found", default="Rule not found."),
                ephemeral=True,
            )
            return
        await self.save_autorole_config(interaction.guild.id, cfg)
        await interaction.response.send_message(
            _autorole_text_sync(
                interaction.guild.id,
                "rule_removed",
                default="Removed rule `{rule_id}`.",
                rule_id=rule_id,
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="test",
        description="Simulate which auto role rules apply to you.",
)
    async def autorole_test(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message(t_sync(interaction.user.id, "common.guild_only"), ephemeral=True)
            return
        cfg = await self.load_autorole_config(interaction.guild.id)
        member = interaction.user
        lines: list[str] = []
        for rule in cfg["rules"]:
            ok, failures = self.check_conditions(member, rule["conditions"])
            if ok:
                assignable = [rid for rid in rule["roles"] if interaction.guild.get_role(rid) and interaction.guild.get_role(rid) not in member.roles]
                lines.append(f"✅ `{rule['id']}` ({rule['event']}) -> would apply {len(assignable)} role(s)")
            else:
                lines.append(f"❌ `{rule['id']}` ({rule['event']}) -> {'; '.join(failures)[:180]}")
        embed = discord.Embed(
            title=_autorole_text_sync(interaction.user.id, "test_title", default="Auto Role Test"),
            color=discord.Color.gold(),
        )
        embed.description = (
            "\n".join(lines)
            if lines
            else _autorole_text_sync(interaction.user.id, "no_rules", default="No rules configured.")
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        await self._process_event_for_member(member.guild, member, "member_join")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or not isinstance(message.author, discord.Member):
            return
        cfg = await self.load_autorole_config(message.guild.id)
        seen = set(cfg.get("first_message_users", []))
        if message.author.id in seen:
            return
        seen.add(message.author.id)
        cfg["first_message_users"] = list(seen)
        await self.save_autorole_config(message.guild.id, cfg)
        await self._process_event_for_member(message.guild, message.author, "first_message")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = payload.member or guild.get_member(payload.user_id)
        if member is None or member.bot:
            return
        await self._process_event_for_member(guild, member, "reaction_add")

    @commands.Cog.listener("on_coffeecord_module_event")
    async def on_coffeecord_module_event(
        self,
        guild: discord.Guild,
        module_name: str,
        action: str,
        actor: Optional[discord.abc.User] = None,
        details: str = "",
        channel_id: Optional[int] = None,
    ) -> None:
        module_key = (module_name or "").strip().lower()
        action_key = (action or "").strip().lower()
        if module_key == "verification" and action_key == "verify_success":
            member = guild.get_member(actor.id) if actor else None
            if member is not None:
                await self._process_event_for_member(guild, member, "verified")
        elif module_key == "leveling" and action_key == "level_up":
            member = guild.get_member(actor.id) if actor else None
            if member is not None:
                await self._process_event_for_member(guild, member, "level_up")

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await self.load_autorole_config(member.guild.id)
        cfg["pending"] = [p for p in cfg.get("pending", []) if int(p.get("member_id", 0)) != member.id]
        await self.save_autorole_config(member.guild.id, cfg)
        for key, task in list(self._pending_tasks.items()):
            gid, mid, _ = key
            if gid == member.guild.id and mid == member.id:
                task.cancel()
                self._pending_tasks.pop(key, None)

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role: discord.Role) -> None:
        cfg = await self.load_autorole_config(role.guild.id)
        changed = False
        for rule in cfg["rules"]:
            if role.id in rule["roles"]:
                rule["roles"] = [rid for rid in rule["roles"] if rid != role.id]
                changed = True
        cfg["rules"] = [r for r in cfg["rules"] if r["roles"]]
        if changed:
            await self.save_autorole_config(role.guild.id, cfg)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if str(guild.id) in self._config:
            async with _CONFIG_LOCK:
                self._config.pop(str(guild.id), None)
                await asyncio.to_thread(_write_config_sync, self._config)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoRoleCog(bot))
