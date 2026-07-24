"""Natural-language slash-command help search and detail views."""
from __future__ import annotations

import re
from typing import Any, NamedTuple

import discord
from discord import app_commands
from Modules.i18n import t, t_sync

HELP_SEARCH_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "to",
        "for",
        "of",
        "my",
        "your",
        "how",
        "do",
        "does",
        "i",
        "me",
        "we",
        "you",
        "someone",
        "something",
        "want",
        "need",
        "can",
        "is",
        "are",
        "what",
        "which",
        "where",
        "when",
        "who",
        "use",
        "using",
        "with",
        "from",
        "into",
        "about",
        "please",
    }
)

HELP_QUERY_SYNONYMS: dict[str, tuple[str, ...]] = {
    "change": ("customize", "edit", "set", "update", "configure", "modify", "adjust"),
    "background": ("backdrop", "wallpaper", "bg", "image", "url"),
    "color": ("colour", "hex", "tint", "shade"),
    "role": ("roles",),
    "ban": ("banned", "blacklist"),
    "kick": ("kicked",),
    "warn": ("warning", "warnings"),
    "log": ("logging", "logs", "audit"),
    "level": ("xp", "rank", "leveling"),
    "card": ("levelcard",),
    "quest": ("quests", "challenge", "mission"),
    "translate": ("translation", "language"),
    "verify": ("verification",),
    "welcome": ("greet", "greeting", "join"),
    "mute": ("timeout", "silence"),
    "delete": ("remove", "clear", "purge"),
    "create": ("add", "new", "make", "setup", "set up"),
    "list": ("show", "view", "display"),
}

HELP_CMD_DESC_MAX = 96
HELP_SEARCH_SELECT_MAX = 25
HELP_SEARCH_LIST_MAX = 15
HELP_ALL_PAGE_SIZE = 20
# Discord caps combined embed payload at 6000 chars per message — send one embed each.
HELP_ALL_EMBEDS_PER_MESSAGE = 1
HELP_SEARCH_ALL_QUERIES = frozenset({"all", "*"})
HELP_SEARCH_I18N_PREFIX = "help_search."


def _i18n_key(name: str) -> str:
    return f"{HELP_SEARCH_I18N_PREFIX}{name}"


def _help_search_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, _i18n_key(key), default=default, **params)


async def _help_search_text(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    if user_id is None:
        return _help_search_text_sync(user_id, key, default=default, **params)
    return await t(user_id, _i18n_key(key), default=default, **params)


class HelpSearchHit(NamedTuple):
    qualified: str
    info: dict[str, Any]
    score: float


def _walk_command_infos(
    items: list[app_commands.Command | app_commands.Group],
    prefix: str = "",
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for cmd in items:
        if isinstance(cmd, app_commands.Group):
            base = f"{prefix}{cmd.name} "
            found.extend(_walk_command_infos(list(cmd.commands), base))
        else:
            qualified = f"/{(prefix + cmd.name).strip()}"
            params: list[dict[str, Any]] = []
            for param in cmd.parameters:
                params.append(
                    {
                        "name": param.name,
                        "description": (param.description or "").strip(),
                        "required": bool(param.required),
                    }
                )
            found.append(
                {
                    "qualified_name": qualified,
                    "description": (cmd.description or "").strip(),
                    "parameters": params,
                }
            )
    return found


def collect_command_infos(command_tree: app_commands.CommandTree) -> list[dict[str, Any]]:
    infos = _walk_command_infos(list(command_tree.get_commands()))
    infos.sort(key=lambda item: str(item.get("qualified_name", "")).lower())
    return infos


def is_list_all_help_query(query: str) -> bool:
    return query.strip().lower() in HELP_SEARCH_ALL_QUERIES


def list_all_help_commands(command_tree: app_commands.CommandTree) -> list[HelpSearchHit]:
    """Return every registered slash command as search hits (alphabetical)."""
    return [
        HelpSearchHit(
            qualified=str(info["qualified_name"]),
            info=info,
            score=1.0,
        )
        for info in collect_command_infos(command_tree)
    ]


def _tokenize_query(query: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", query.lower())
    return [token for token in raw if len(token) > 1 and token not in HELP_SEARCH_STOPWORDS]


def _command_search_haystack(info: dict[str, Any]) -> str:
    parts: list[str] = [
        str(info.get("qualified_name", "")),
        str(info.get("description", "")),
    ]
    qn = str(info.get("qualified_name", "")).lstrip("/")
    parts.append(qn.replace("_", " "))
    parts.append(qn.replace(" ", ""))
    for param in info.get("parameters") or []:
        if isinstance(param, dict):
            parts.append(str(param.get("name", "")))
            parts.append(str(param.get("description", "")))
    return " ".join(parts).lower()


def _score_command(info: dict[str, Any], tokens: list[str]) -> float:
    if not tokens:
        return 0.0
    haystack = _command_search_haystack(info)
    qn_compact = str(info.get("qualified_name", "")).lower().replace("/", " ").replace("_", "").replace(" ", "")
    score = 0.0
    for token in tokens:
        token_hit = False
        if token in haystack:
            score += 1.0
            token_hit = True
        else:
            for synonym in HELP_QUERY_SYNONYMS.get(token, ()):
                if synonym in haystack:
                    score += 0.85
                    token_hit = True
                    break
        if token in qn_compact or token in str(info.get("qualified_name", "")).lower():
            score += 0.2
        if not token_hit and len(token) >= 4:
            for word in re.findall(r"[a-z0-9]+", haystack):
                if word.startswith(token) or token.startswith(word):
                    score += 0.35
                    break
    if score <= 0:
        return 0.0
    coverage = score / float(len(tokens))
    return score + coverage * 0.5


def search_help_commands_intent(
    command_tree: app_commands.CommandTree,
    query: str,
    *,
    limit: int = 40,
) -> list[HelpSearchHit]:
    needle = query.strip()
    if not needle:
        return []

    catalog = collect_command_infos(command_tree)
    tokens = _tokenize_query(needle)
    hits: list[HelpSearchHit] = []

    if tokens:
        for info in catalog:
            score = _score_command(info, tokens)
            if score > 0:
                hits.append(
                    HelpSearchHit(
                        qualified=str(info["qualified_name"]),
                        info=info,
                        score=score,
                    )
                )
        hits.sort(key=lambda item: (-item.score, item.qualified.lower()))

    if not hits:
        lowered = needle.lower()
        for info in catalog:
            haystack = _command_search_haystack(info)
            if lowered in haystack:
                hits.append(
                    HelpSearchHit(
                        qualified=str(info["qualified_name"]),
                        info=info,
                        score=1.0,
                    )
                )
        hits.sort(key=lambda item: item.qualified.lower())

    return hits[:limit]


def search_help_commands(
    command_tree: app_commands.CommandTree,
    query: str,
) -> list[tuple[str, str]]:
    """Backward-compatible (qualified, description) pairs for help search."""
    return [
        (hit.qualified, str(hit.info.get("description", "")))
        for hit in search_help_commands_intent(command_tree, query)
    ]


def format_command_usage_line(info: dict[str, Any]) -> str:
    qualified = str(info.get("qualified_name", "")).lstrip("/")
    params = info.get("parameters") or []
    if not params:
        return f"`/{qualified}`"
    parts: list[str] = []
    for param in params:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name", ""))
        if param.get("required", True):
            parts.append(f"{name}:<value>")
        else:
            parts.append(f"[{name}:<value>]")
    return f"`/{qualified} {' '.join(parts)}`"


async def build_command_detail_embed(info: dict[str, Any], *, user_id: int | None = None) -> discord.Embed:
    qualified = str(info.get("qualified_name", "/unknown"))
    description = str(info.get("description", "")).strip() or await _help_search_text(
        user_id,
        "detail_no_description",
        default="No description.",
    )
    embed = discord.Embed(
        title=qualified,
        description=description,
        color=discord.Color.blurple(),
    )
    params = info.get("parameters") or []
    if params:
        lines: list[str] = []
        for param in params:
            if not isinstance(param, dict):
                continue
            name = str(param.get("name", ""))
            req = await _help_search_text(
                user_id,
                "detail_required",
                default="required",
            ) if param.get("required", True) else await _help_search_text(
                user_id,
                "detail_optional",
                default="optional",
            )
            desc = str(param.get("description", "")).strip() or await _help_search_text(
                user_id,
                "detail_param_empty",
                default="—",
            )
            lines.append(f"**{name}** ({req}) — {desc}")
        embed.add_field(
            name=await _help_search_text(user_id, "detail_parameters", default="Parameters"),
            value="\n".join(lines)[:1024],
            inline=False,
        )
    embed.add_field(
        name=await _help_search_text(user_id, "detail_usage", default="Usage"),
        value=format_command_usage_line(info),
        inline=False,
    )
    return embed


def _format_hit_line(hit: HelpSearchHit) -> str:
    desc = str(hit.info.get("description", "")).strip()
    if desc:
        short = desc if len(desc) <= HELP_CMD_DESC_MAX else desc[: HELP_CMD_DESC_MAX - 1] + "…"
        return f"**{hit.qualified}** — {short}"
    return f"**{hit.qualified}**"


async def build_all_commands_pages(
    hits: list[HelpSearchHit],
    *,
    user_id: int | None = None,
) -> list[discord.Embed]:
    """Split every slash command into embeds for /help search:all."""
    if not hits:
        return []

    total = len(hits)
    page_count = max(1, (total + HELP_ALL_PAGE_SIZE - 1) // HELP_ALL_PAGE_SIZE)
    pages: list[discord.Embed] = []
    for page_index in range(page_count):
        start = page_index * HELP_ALL_PAGE_SIZE
        chunk = hits[start : start + HELP_ALL_PAGE_SIZE]
        lines = [_format_hit_line(hit) for hit in chunk]
        title = await _help_search_text(
            user_id,
            "all_title",
            default="All commands ({current}/{total})",
            current=str(page_index + 1),
            total=str(page_count),
        )
        if page_index == 0:
            description = (
                await _help_search_text(
                    user_id,
                    "all_intro",
                    default="Every registered slash command ({count}):",
                    count=str(total),
                )
                + "\n\n"
                + "\n".join(lines)
            )
        else:
            description = "\n".join(lines)
        embed = discord.Embed(
            title=title,
            description=description,
            color=discord.Color.green(),
        )
        embed.set_footer(
            text=await _help_search_text(
                user_id,
                "all_footer",
                default="Part {current}/{total} • Use /help search:<topic> for details",
                current=str(page_index + 1),
                total=str(page_count),
            )
        )
        pages.append(embed)
    return pages


def chunk_embeds_for_messages(
    embeds: list[discord.Embed],
    *,
    per_message: int = HELP_ALL_EMBEDS_PER_MESSAGE,
) -> list[list[discord.Embed]]:
    """Split embeds across messages.

    Discord limits combined embed content to 6000 characters per message, so the
    default is one embed per message for /help search:all.
    """
    size = max(1, min(per_message, 10))
    return [embeds[i : i + size] for i in range(0, len(embeds), size)]


async def build_search_results_embed(
    query: str,
    hits: list[HelpSearchHit],
    *,
    user_id: int | None = None,
) -> discord.Embed:
    lines = [_format_hit_line(hit) for hit in hits[:HELP_SEARCH_LIST_MAX]]
    extra = ""
    if len(hits) > HELP_SEARCH_LIST_MAX:
        extra = await _help_search_text(
            user_id,
            "results_more",
            default="\n\n…and {count} more. Refine your search.",
            count=str(len(hits) - HELP_SEARCH_LIST_MAX),
        )

    embed = discord.Embed(
        title=await _help_search_text(user_id, "results_title", default="Search: {query}", query=query),
        description=(
            await _help_search_text(
                user_id,
                "results_intro",
                default="Commands that match what you want to do:",
            ) + "\n\n" + "\n".join(lines) + extra
            if lines
            else await _help_search_text(user_id, "results_none", default="No commands found.")
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(
        text=await _help_search_text(
            user_id,
            "results_footer",
            default="Use the menu below for full details on a command.",
        )
    )
    return embed


class HelpSearchDetailSelect(discord.ui.Select):
    def __init__(self, hits: list[HelpSearchHit], *, user_id: int | None = None) -> None:
        self._catalog = {hit.qualified: hit.info for hit in hits}
        self._user_id = user_id
        options: list[discord.SelectOption] = []
        for hit in hits[:HELP_SEARCH_SELECT_MAX]:
            desc = str(hit.info.get("description", "")).strip()
            options.append(
                discord.SelectOption(
                    label=hit.qualified.lstrip("/")[:100],
                    description=(
                        desc[:100]
                        if desc
                        else _help_search_text_sync(
                            user_id,
                            "detail_select_option_fallback",
                            default="View command details",
                        )
                    ),
                    value=hit.qualified,
                )
            )
        super().__init__(
            placeholder=_help_search_text_sync(
                user_id,
                "detail_select_placeholder",
                default="More info about a command…",
            ),
            min_values=1,
            max_values=1,
            options=options,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        qualified = self.values[0]
        info = self._catalog.get(qualified)
        user_id = interaction.user.id
        if info is None:
            await interaction.response.send_message(
                await _help_search_text(
                    user_id,
                    "detail_missing",
                    default="That command is no longer available.",
                ),
                ephemeral=True,
            )
            return
        embed = await build_command_detail_embed(info, user_id=user_id)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class HelpSearchResultsView(discord.ui.View):
    def __init__(self, hits: list[HelpSearchHit], *, user_id: int | None = None) -> None:
        super().__init__(timeout=180)
        if hits:
            self.add_item(HelpSearchDetailSelect(hits, user_id=user_id))
