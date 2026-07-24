"""Paginated getting-started embeds for /getting_started and the guild welcome button."""

from __future__ import annotations

import discord

from Modules.i18n import DEFAULT_LOCALE, get_user_language_sync, t_for_locale

GETTING_STARTED_EMBED_COLOR = discord.Color.from_str("#7B5EA7")
_FIELD_MAX = 1024
_TOTAL_PAGES = 8
_FIELDS_PER_PAGE = 3


def _field(name: str, value: str, *, inline: bool = False) -> dict[str, object]:
    text = value.strip()
    if len(text) > _FIELD_MAX:
        text = text[: _FIELD_MAX - 1] + "…"
    return {"name": name, "value": text, "inline": inline}


def _tx(locale: str, key: str, default: str, /, **params: object) -> str:
    return t_for_locale(
        locale,
        key,
        default=default,
        **{name: str(value) for name, value in params.items()},
    )


def _page(title: str, description: str, *fields: dict[str, object], footer: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description.strip(),
        color=GETTING_STARTED_EMBED_COLOR,
    )
    for field_data in fields:
        embed.add_field(
            name=str(field_data["name"]),
            value=str(field_data["value"]),
            inline=bool(field_data.get("inline", False)),
        )
    if footer:
        embed.set_footer(text=footer)
    return embed


def build_getting_started_pages(user_id: int | None = None) -> list[discord.Embed]:
    """Multi-page onboarding guide (use with prev/next buttons)."""
    locale = DEFAULT_LOCALE if user_id is None else get_user_language_sync(int(user_id))
    total = _TOTAL_PAGES
    pages: list[discord.Embed] = []

    for page_index in range(1, total + 1):
        fields: list[dict[str, object]] = []
        for field_index in range(1, _FIELDS_PER_PAGE + 1):
            fields.append(
                _field(
                    _tx(
                        locale,
                        f"getting_started.page{page_index}.field{field_index}.name",
                        f"Section {field_index}",
                        current=page_index,
                        total=total,
                    ),
                    _tx(
                        locale,
                        f"getting_started.page{page_index}.field{field_index}.value",
                        "-",
                        current=page_index,
                        total=total,
                    ),
                )
            )

        pages.append(
            _page(
                _tx(
                    locale,
                    f"getting_started.page{page_index}.title",
                    f"Getting Started ({page_index}/{total})",
                    current=page_index,
                    total=total,
                ),
                _tx(
                    locale,
                    f"getting_started.page{page_index}.description",
                    "",
                    current=page_index,
                    total=total,
                ),
                *fields,
                footer=_tx(
                    locale,
                    f"getting_started.page{page_index}.footer",
                    f"Page {page_index}/{total}",
                    current=page_index,
                    total=total,
                ),
            )
        )

    return pages
