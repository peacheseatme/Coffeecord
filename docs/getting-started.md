# Getting started with Coffeecord

This guide walks through configuring Coffeecord on a new Discord server. In Discord you can open the same content anytime with **`/getting_started`** (paginated embeds) or press **Getting Started** on the bot’s guild-join welcome message.

## Prerequisites

Before running setup commands:

1. **Invite Coffeecord** with permissions to manage roles, channels, messages, and members (Manage Server is typical for admins running setup).
2. **Role hierarchy** — In **Server Settings → Roles**, drag Coffeecord’s role **above** any role it must assign (verified, mute, color roles, level rewards, reaction roles).
3. **Staff access** — Users who configure the bot need **Manage Server** or **Administrator** unless you later relax permissions with `/command_perms edit`.

Recommended channels to create early:

- `#mod-log` — logging and automod output
- `#welcome` — join messages
- `#verify` — verification panel (if you use verification)

---

## Language

Each member can choose the language Coffeecord uses for bot replies:

| Command | Purpose |
|---------|---------|
| `/language status` | Show your current language |
| `/language set` | Set your language (English, Español, Português, Русский) |

---

## Quick path: setup wizard

The fastest way to configure common features is the interactive wizard:

| Command | Purpose |
|---------|---------|
| `/setup` | Start quick setup (welcome, leave, logging, automod, reaction roles, tickets) |
| `/setup_resume` | Continue a saved draft |
| `/setup_cancel` | Discard the draft |

The wizard lets you select which areas to configure, step through each one, and **confirm before anything is written**. Skipped features are left unchanged.

Check module availability with **`/modules status`**. Disable features you do not need with **`/modules disable`**.

---

## Logging

Logging sends structured events to one staff channel.

| Command | Purpose |
|---------|---------|
| `/logging setup` | Choose log channel and enable logging |
| `/logging status` | Current channel, events, and modules |
| `/logging toggle` | Enable/disable individual event types |
| `/logging module` | Toggle logging per feature area |
| `/logging disable` | Turn off logging for the server |

**Suggested first events:** `member_join`, `member_leave`, `automod`, `timeout`, `ban`.

Use a private staff channel. Pair with **`/automod set log`** for dedicated automod logs if desired.

---

## Verification and member onboarding

### Verification

**`/verifyconfig`** configures:

- **Method** — Simple Button, Keypad Code, or Color Buttons
- **Verified role** — granted after success
- **Verify channel** — where the panel is posted
- **Log channel** — verification audit trail

The bot posts a persistent **Verify Me** button. After a bot restart, panels keep working (persistent views are re-registered on startup).

### Auto roles

| Command | Purpose |
|---------|---------|
| `/autorole status` | View rules |
| `/autorole add` | Create a rule (interactive) |
| `/autorole toggle` | Enable/disable autorole |
| `/autorole test` | Preview rules for yourself |

### Welcome and leave

| Command | Purpose |
|---------|---------|
| `/welcome config` | Welcome channel, message, delivery mode |
| `/welcome test` | Send a test welcome |
| `/leave config` | Leave message and optional exit survey |
| `/leave test` | Send a test leave message |

---

## Moderation and safety

| Command | Purpose |
|---------|---------|
| `/automod overview` | Status and enabled rules |
| `/automod on` / `/automod off` | Enable or disable automod |
| `/automod preset` | Apply a preset (Strict, Relaxed, etc.) |
| `/ban`, `/mute`, `/unmute` | Core moderation (see `/help`) |
| `/purge`, `/lockdown` | Staff utilities module |

---

## Engagement

| Command | Purpose |
|---------|---------|
| `/modules` | Enable leveling, quests, translation, etc. |
| `/xp config` | XP rates and leveling settings |
| `/quests list` | Server quests |
| `/translate settings` | Live translation preferences |

---

## Need more detail?

- **`/help`** — browse or search commands
- **`/getting_started`** — full paginated guide in Discord
- **[Permissions doc](commands/checks-and-permissions.md)** — slash permission overrides
