# Coffeecord

A modular Discord bot with leveling, automod, tickets, verification, Ko-fi integration, and more.

## Features

- **Moderation** — Automod (spam, caps, links, mentions, bad words), mute role, verification
- **Engagement** — Leveling & XP, reaction roles, polls, fun commands (8ball, hug, dog, cat, etc.)
- **Utilities** — Tickets, staff applications, private calls, translation, nickname management
- **Configuration** — Auto roles, logging, welcome/leave messages, per-server module toggles
- **Integrations** — Ko-fi supporter perks and linking

## Quick Start

1. [Install the bot](docs/installation/README.md) — choose your platform:
   - [Install script (Linux/macOS)](docs/installation/install-script.md) — recommended
   - [Manual Linux](docs/installation/linux.md)
   - [Windows](docs/installation/windows.md)
2. Invite the bot to your server (replace `YOUR_CLIENT_ID` with your bot's application ID):
   ```
   https://discord.com/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=8&scope=bot+applications.commands
   ```
3. Run `c-cord start` to start the bot.

## Daily Usage

```bash
c-cord start      # Start the bot
c-cord stop       # Stop the bot
c-cord restart    # Restart the bot
c-cord status     # Check status and uptime
c-cord logs       # Watch live output
c-cord update              # Latest GitHub Release (git or zipball) → restart
c-cord update 1.0.3        # Specific release tag
c-cord update --branch     # git pull main (development)
```

## Documentation

- [Installation guide](docs/installation/README.md) — Script, Linux, Windows
- [CLI reference](docs/architecture/cli-reference.md) — All c-cord commands
- [Module docs](docs/modules/README.md) — Creating and managing modules
- [Full documentation](docs/README.md) — Architecture, commands, storage

## Requirements

- Python 3.12+
- Discord bot token ([Discord Developer Portal](https://discord.com/developers/applications))

## License

See repository for license details.

Cursor and ChatGPt helped me bc I'm still learning to code :)