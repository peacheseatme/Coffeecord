#!/usr/bin/env python3
"""Apply i18n replacements to Src/Bot.py (run once after adding catalog keys)."""
from __future__ import annotations

from pathlib import Path

BOT = Path(__file__).resolve().parent.parent / "Src" / "Bot.py"

# (old, new) — longest / most specific first
REPLACEMENTS: list[tuple[str, str]] = [
    # command_perms
    (
        'title="Command Permissions",',
        'title=await _user_text(interaction, "bot.command_perms.docs_title", default="Command Permissions"),',
    ),
    (
        """description=(
            "Full reference: which slash commands use which **member** permissions, and what **bot** permissions "
            "features need. Use `/command_perms edit` (Manage Server) to type any slash command name and set requirements."
        ),""",
        """description=await _user_text(
            interaction,
            "bot.command_perms.docs_description",
            default=(
                "Full reference: which slash commands use which **member** permissions, and what **bot** "
                "permissions features need. Use `/command_perms edit` (Manage Server) to type any slash command name and set requirements."
            ),
        ),""",
    ),
    (
        'name="Quick links",',
        'name=await _user_text(interaction, "bot.command_perms.docs_quick_links", default="Quick links"),',
    ),
    (
        'text="Per-guild overrides apply after the next sync; defaults ship with the bot."',
        'text=await _user_text(interaction, "bot.command_perms.docs_footer", default="Per-guild overrides apply after the next sync; defaults ship with the bot.")',
    ),
    (
        'label="Open permissions doc"',
        'label=await _user_text(interaction, "bot.command_perms.docs_button", default="Open permissions doc")',
    ),
    (
        '"No overrides in this server. Commands use bot defaults (see `/command_perms docs`).",',
        'await _user_text(interaction, "bot.command_perms.list_empty", default="No overrides in this server. Commands use bot defaults (see `/command_perms docs`)."),',
    ),
    (
        'title="Command permission overrides",',
        'title=await _user_text(interaction, "bot.command_perms.list_title", default="Command permission overrides"),',
    ),
    (
        '"❌ No matching slash command. Use the name Discord shows (e.g. `help`, `call create`, `modules toggle`).",',
        'f"❌ {await _user_text(interaction, \'bot.command_perms.edit_no_match\', default=\'No matching slash command. Use the name Discord shows (e.g. help, call create, modules toggle).\')}",',
    ),
    (
        '"❌ For **Custom**, fill `custom_permissions` (comma-separated permission names).",',
        'f"❌ {await _user_text(interaction, \'bot.command_perms.edit_custom_required\', default=\'For Custom, fill custom_permissions (comma-separated permission names).\')}",',
    ),
    (
        'msg = f"✅ Reset **`/{qn}`** to the bot default."',
        'msg = f"✅ {await _user_text(interaction, \'bot.command_perms.edit_reset_default\', default=\'Reset /{command} to the bot default.\', command=qn)}"',
    ),
    (
        'msg = f"✅ Removed custom rules for **`/{qn}`** (no CoffeeCord member-permission gate)."',
        'msg = f"✅ {await _user_text(interaction, \'bot.command_perms.edit_removed\', default=\'Removed custom rules for /{command}.\', command=qn)}"',
    ),
    (
        'msg = f"✅ **`/{qn}`** now requires: {command_perm_overrides.format_rule_human(rule)}"',
        'msg = f"✅ {await _user_text(interaction, \'bot.command_perms.edit_set\', default=\'/{command} now requires: {rule}\', command=qn, rule=command_perm_overrides.format_rule_human(rule))}"',
    ),
    # kofi slash
    (
        '"❌ Please provide a valid email address.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.kofi.invalid_email\', default=\'Please provide a valid email address.\')}", ephemeral=True',
    ),
    (
        '"You are not marked as a supporter yet.", ephemeral=True',
        'await _user_text(interaction, "bot.kofi.not_supporter", default="You are not marked as a supporter yet."), ephemeral=True',
    ),
    (
        'title="Ko-fi Supporter Status"',
        'title=await _user_text(interaction, "bot.kofi.status_title", default="Ko-fi Supporter Status")',
    ),
    (
        'name="Tier"',
        'name=await _user_text(interaction, "bot.kofi.field_tier", default="Tier")',
    ),
    (
        'name="Last Payment"',
        'name=await _user_text(interaction, "bot.kofi.field_last_payment", default="Last Payment")',
    ),
    (
        'name="Total USD"',
        'name=await _user_text(interaction, "bot.kofi.field_total_usd", default="Total USD")',
    ),
    (
        'f"✅ {user.mention} marked as an active supporter.", ephemeral=True',
        'await _user_text(interaction, "bot.kofi.add_success", default="{user} marked as an active supporter.", user=user.mention), ephemeral=True',
    ),
    (
        '"❌ No supporter record exists for that user.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.kofi.remove_no_record\', default=\'No supporter record exists for that user.\')}", ephemeral=True',
    ),
    (
        'f"✅ Supporter status disabled for {user.mention}.", ephemeral=True',
        'await _user_text(interaction, "bot.kofi.remove_success", default="Supporter status disabled for {user}.", user=user.mention), ephemeral=True',
    ),
    # kofi prefix
    (
        'await ctx.send("❌ Please provide a valid email address.")',
        'await ctx.send(f"❌ {t_sync(ctx.author.id, \'bot.kofi.invalid_email\', default=\'Please provide a valid email address.\')}")',
    ),
    (
        'await ctx.send("You are not marked as a supporter yet.")',
        'await ctx.send(t_sync(ctx.author.id, "bot.kofi.not_supporter", default="You are not marked as a supporter yet."))',
    ),
    (
        'await ctx.send("❌ No supporter record exists for that user.")',
        'await ctx.send(f"❌ {t_sync(ctx.author.id, \'bot.kofi.remove_no_record\', default=\'No supporter record exists for that user.\')}")',
    ),
    # poll / verify / call / timers / say / dm
    (
        '"📊 Choose a channel to send the poll:", view=PollChannelView(), ephemeral=True',
        'await _user_text(interaction, "bot.poll.choose_channel", default="📊 Choose a channel to send the poll:"), view=PollChannelView(), ephemeral=True',
    ),
    (
        'await channel.send("🛑 Poll ended! Thanks for voting.")',
        'await channel.send(await t(interaction.user.id, "bot.poll.ended", default="🛑 Poll ended! Thanks for voting."))',
    ),
    (
        'await channel.send("⚠️ I do not have permission to clear reactions.")',
        'await channel.send(await t(interaction.user.id, "bot.poll.clear_reactions_denied", default="⚠️ I do not have permission to clear reactions."))',
    ),
    (
        '"❌ This isn\'t for you.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.wrong_user\', default=\'This isn\\\'t for you.\')}", ephemeral=True',
    ),
    (
        '"❌ This isn\'t your verification session.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.color_wrong_session\', default=\'This isn\\\'t your verification session.\')}", ephemeral=True',
    ),
    (
        '"❌ Not your session.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.color_not_your_session\', default=\'❌ Not your session.\')}", ephemeral=True',
    ),
    (
        '"✅ Verification successful!", ephemeral=True',
        'await _user_text(interaction, "bot.verify.code_success", default="✅ Verification successful!"), ephemeral=True',
    ),
    (
        '"❌ Incorrect code. Try again later.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.code_wrong\', default=\'Incorrect code. Try again later.\')}", ephemeral=True',
    ),
    (
        '"❌ Incorrect color. Try again!", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.color_wrong\', default=\'Incorrect color. Try again!\')}", ephemeral=True',
    ),
    (
        '"❌ Invalid verification method.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.verify.invalid_method\', default=\'Invalid verification method.\')}", ephemeral=True',
    ),
    (
        '"❌ I don\'t have permission to change my nickname.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.nickname.no_permission\', default=\'I don\\\'t have permission to change my nickname.\')}", ephemeral=True',
    ),
    (
        '"Select a channel to send a message:", view=SayView(interaction.guild), ephemeral=True',
        'await _user_text(interaction, "bot.say.choose_channel", default="Select a channel to send a message:"), view=SayView(interaction.guild), ephemeral=True',
    ),
    (
        '"Select a user to DM:", view=DmView(interaction.guild), ephemeral=True',
        'await _user_text(interaction, "bot.dm_cmd.choose_member", default="Select a user to DM:"), view=DmView(interaction.guild), ephemeral=True',
    ),
    (
        '"❌ User not found.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.dm_cmd.user_not_found\', default=\'User not found.\')}", ephemeral=True',
    ),
    (
        '"❌ No autoroles to remove.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.autorole_legacy.no_roles\', default=\'No autoroles to remove.\')}", ephemeral=True',
    ),
    (
        '"Toggled staff applications!", ephemeral=True',
        'await _user_text(interaction, "bot.applications.toggled", default="Toggled staff applications!"), ephemeral=True',
    ),
    (
        '"Select question to delete:", view=view, ephemeral=True',
        'await _user_text(interaction, "bot.applications.choose_delete", default="Select question to delete:"), view=view, ephemeral=True',
    ),
    (
        '"Uninstall cancelled.", ephemeral=True',
        'await _user_text(interaction, "bot.uninstall.cancelled", default="Uninstall cancelled."), ephemeral=True',
    ),
    # call
    (
        'return await interaction.followup.send("❌ Guild not found.", ephemeral=True)',
        'return await interaction.followup.send(f"❌ {await _user_text(interaction, \'bot.call.guild_not_found\', default=\'Guild not found.\')}", ephemeral=True)',
    ),
    (
        'return await interaction.followup.send("❌ Could not resolve your user for this command.", ephemeral=True)',
        'return await interaction.followup.send(f"❌ {await _user_text(interaction, \'bot.call.user_not_found\', default=\'Could not resolve your user for this command.\')}", ephemeral=True)',
    ),
    (
        'return await interaction.response.send_message("❌ Incorrect password.", ephemeral=True)',
        'return await interaction.response.send_message(f"❌ {await _user_text(interaction, \'bot.call.join_wrong_password\', default=\'Incorrect password.\')}", ephemeral=True)',
    ),
    (
        '"❌ You aren\'t the host of any active call.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.call.not_host\', default=\'You aren\\\'t the host of any active call.\')}", ephemeral=True',
    ),
    (
        '"❌ Call channel no longer exists.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.call.channel_gone\', default=\'Call channel no longer exists.\')}", ephemeral=True',
    ),
    (
        '"❌ You don\'t currently host any active call.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.call.no_active_host\', default=\'You don\\\'t currently host any active call.\')}", ephemeral=True',
    ),
    (
        '"📞 Call ended."',
        'await _user_text(interaction, "bot.call.end_success", default="📞 Call ended.")',
    ),
    (
        '"❌ That user isn\'t in the call.", ephemeral=True',
        'f"❌ {await _user_text(interaction, \'bot.call.remove_not_in_call\', default=\'That user isn\\\'t in the call.\')}", ephemeral=True',
    ),
    # timers
    (
        '"\\u274C Invalid format. Use like `10s`, `5m`, or `2h`.", ephemeral=True',
        'f"\\u274C {await _user_text(interaction, \'bot.timers.invalid_format\', default=\'Invalid format. Use like 10s, 5m, 2h, or 1d.\')}", ephemeral=True',
    ),
    (
        '"\\u274C You have no active timers.", ephemeral=True',
        'f"\\u274C {await _user_text(interaction, \'bot.timers.no_timers\', default=\'You have no active timers.\')}", ephemeral=True',
    ),
    (
        '"\\u274C Timer not found.", ephemeral=True',
        'f"\\u274C {await _user_text(interaction, \'bot.timers.timer_not_found\', default=\'Timer not found or not yours.\')}", ephemeral=True',
    ),
    # mute hardcoded
    (
        '"⚠️ Mute role not set. Use `/muterole_create` or `/muterole_update`.", ephemeral=True',
        'f"⚠️ {await _user_text(interaction, \'moderation.mute.role_not_set\', default=\'Mute role not set. Use /muterole create or /muterole update.\')}", ephemeral=True',
    ),
    (
        '"⚠️ No mute role configured.", ephemeral=True',
        'f"⚠️ {await _user_text(interaction, \'moderation.mute.no_role_configured\', default=\'No mute role configured.\')}", ephemeral=True',
    ),
    (
        '"⚠️ Mute role not set.", ephemeral=True',
        'f"⚠️ {await _user_text(interaction, \'moderation.mute.role_not_set_short\', default=\'Mute role not set.\')}", ephemeral=True',
    ),
]


def main() -> None:
    text = BOT.read_text(encoding="utf-8")
    applied = 0
    for old, new in REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
            applied += 1
            print(f"applied: {old[:60]}...")
        else:
            print(f"SKIP (not found): {old[:60]}...")
    BOT.write_text(text, encoding="utf-8")
    print(f"Done. {applied}/{len(REPLACEMENTS)} replacements applied.")


if __name__ == "__main__":
    main()
