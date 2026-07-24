"""
Fun commands: 8ball, bet, flipcoin, hug, kiss, lovecalc, truth, dare,
dog, cat, petpet, ak47, uwuify, nuke, roast, abracadaberamotherafu.
"""

import io
import json
import random
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from Modules.i18n import t, t_sync
from . import anti_abuse
from .module_registry import is_module_enabled
from .themes import get_command_response_for_interaction

__module_display_name__ = "Fun Commands"
__module_description__ = "8ball, flipcoin, hug, kiss, dog, cat, roast, and other fun commands."
__module_category__ = "engagement"

FUN_I18N_PREFIX = "fun."

DOG_CEO_RANDOM_URL = "https://dog.ceo/api/breeds/image/random"
DOG_CEO_BREED_URL = "https://dog.ceo/api/breed/{breed}/images/random"
THEDOGAPI_SEARCH_URL = "https://api.thedogapi.com/v1/images/search"
RANDOM_DOG_URL = "https://random.dog/woof.json"
THEDOGAPI_BREEDS_FILE = Path(__file__).resolve().parent / "data" / "thedogapi_breeds.json"
FUN_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=12)

DOG_CEO_BREED_ALIASES: dict[str, str] = {
    "lab": "retriever/labrador",
    "labrador": "retriever/labrador",
    "labradorretriever": "retriever/labrador",
    "golden": "retriever/golden",
    "goldenretriever": "retriever/golden",
    "germanshepherd": "shepherd/german",
    "germanshepherddog": "shepherd/german",
    "gsd": "shepherd/german",
    "husky": "husky",
    "siberianhusky": "husky",
    "frenchbulldog": "bulldog/french",
    "englishbulldog": "bulldog/english",
    "cocker": "spaniel/cocker",
    "cockerspaniel": "spaniel/cocker",
    "corgi": "corgi/pembroke",
    "pembroke": "corgi/pembroke",
    "cardigan": "corgi/cardigan",
    "collie": "collie/border",
    "bordercollie": "collie/border",
    "shiba": "shiba",
    "shibainu": "shiba",
    "bulldog": "bulldog/french",
    "poodle": "poodle/standard",
}

BREED_ALIAS_TO_ID: dict[str, int] = {
    "lab": 149,
    "labrador": 149,
    "labradorretriever": 149,
    "golden": 121,
    "goldenretriever": 121,
    "retriever": 149,
    "husky": 226,
    "siberianhusky": 226,
    "gsd": 115,
    "germanshepherd": 115,
    "germanshepherddog": 115,
    "shepherd": 115,
    "pug": 201,
    "beagle": 31,
    "corgi": 184,
    "pembroke": 184,
    "cardigan": 68,
    "collie": 50,
    "bordercollie": 50,
    "bulldog": 113,
    "frenchbulldog": 113,
    "poodle": 196,
    "pitbull": 15,
    "pit": 15,
    "shiba": 222,
    "shibainu": 222,
    "cocker": 86,
    "cockerspaniel": 86,
    "dalmatian": 92,
    "rottweiler": 210,
    "boxer": 55,
    "doberman": 94,
    "greatdane": 124,
    "maltese": 161,
    "pomeranian": 193,
    "akita": 6,
    "samoyed": 214,
    "yorkshire": 264,
    "yorkie": 264,
}


def _fun_text_sync(user_id: int | None, key: str, *, default: str, **params: str) -> str:
    return t_sync(user_id, f"{FUN_I18N_PREFIX}{key}", default=default, **params)


def _normalize_breed_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def _load_breed_indexes() -> tuple[dict[str, int], dict[str, list[int]], dict[int, str]]:
    name_to_id: dict[str, int] = {}
    word_to_ids: dict[str, list[int]] = {}
    image_by_id: dict[int, str] = {}
    try:
        with THEDOGAPI_BREEDS_FILE.open(encoding="utf-8") as fh:
            breeds = json.load(fh)
    except (OSError, json.JSONDecodeError, TypeError):
        return dict(BREED_ALIAS_TO_ID), {}, {}
    if not isinstance(breeds, list):
        return dict(BREED_ALIAS_TO_ID), {}, {}
    for item in breeds:
        if not isinstance(item, dict) or "id" not in item or "name" not in item:
            continue
        breed_id = int(item["id"])
        breed_name = str(item["name"])
        key = _normalize_breed_key(breed_name)
        if key:
            name_to_id[key] = breed_id
        image = item.get("image")
        if isinstance(image, dict):
            image_url = str(image.get("url", "")).strip()
            if image_url:
                image_by_id[breed_id] = image_url
        if breed_id not in image_by_id:
            ref_id = str(item.get("reference_image_id", "")).strip()
            if ref_id:
                image_by_id[breed_id] = f"https://cdn2.thedogapi.com/images/{ref_id}.jpg"
        for word in re.split(r"[\s/()\-]+", breed_name):
            word_key = _normalize_breed_key(word)
            if len(word_key) < 3:
                continue
            bucket = word_to_ids.setdefault(word_key, [])
            if breed_id not in bucket:
                bucket.append(breed_id)
    for alias, breed_id in BREED_ALIAS_TO_ID.items():
        name_to_id.setdefault(alias, breed_id)
    return name_to_id, word_to_ids, image_by_id


_BREED_NAME_TO_ID, _BREED_WORD_TO_IDS, _BREED_ID_TO_IMAGE_URL = _load_breed_indexes()


def _resolve_thedogapi_breed_id(breed: str) -> int | None:
    token = _normalize_breed_key(breed)
    if not token:
        return None
    if token in BREED_ALIAS_TO_ID:
        return BREED_ALIAS_TO_ID[token]
    if token in _BREED_NAME_TO_ID:
        return _BREED_NAME_TO_ID[token]
    starts = [breed_id for name, breed_id in _BREED_NAME_TO_ID.items() if name.startswith(token)]
    if len(starts) == 1:
        return starts[0]
    word_ids = _BREED_WORD_TO_IDS.get(token, [])
    if len(word_ids) == 1:
        return word_ids[0]
    contains = [breed_id for name, breed_id in _BREED_NAME_TO_ID.items() if token in name]
    if len(contains) == 1:
        return contains[0]
    return None


def _resolve_dog_ceo_breed_path(breed: str) -> str | None:
    token = _normalize_breed_key(breed)
    if not token:
        return None
    if token in DOG_CEO_BREED_ALIASES:
        return DOG_CEO_BREED_ALIASES[token]
    if token in _BREED_NAME_TO_ID:
        return token
    return None


async def _fetch_json(session: aiohttp.ClientSession, url: str, *, params: dict[str, str] | None = None):
    async with session.get(url, params=params, timeout=FUN_HTTP_TIMEOUT) as resp:
        if resp.status != 200:
            return None
        return await resp.json(content_type=None)


async def _check_fun_enabled(interaction: discord.Interaction) -> bool:
    """Return False if module disabled; sends message and returns False."""
    if interaction.guild is None:
        return True
    if not await is_module_enabled(interaction.guild.id, "fun"):
        await interaction.response.send_message(
            await t(interaction.user.id, "common.module_disabled"),
            ephemeral=True,
        )
        return False
    return True


@asynccontextmanager
async def _http_session(bot: commands.Bot):
    """Yield shared aiohttp session or a temporary one."""
    session = getattr(bot, "http_session", None)
    if session is not None and not session.closed:
        yield session
        return
    session = aiohttp.ClientSession()
    try:
        yield session
    finally:
        await session.close()


async def _fetch_dog_image_url(session: aiohttp.ClientSession, breed: str | None = None) -> str | None:
    """Fetch a dog image URL, falling back when dog.ceo is unreachable."""
    breed_norm = _normalize_breed_key(breed or "")

    if breed_norm:
        breed_id = _resolve_thedogapi_breed_id(breed or "")
        if breed_id is None:
            return None

        ceo_path = _resolve_dog_ceo_breed_path(breed or "")
        if ceo_path:
            try:
                data = await _fetch_json(session, DOG_CEO_BREED_URL.format(breed=ceo_path))
                if isinstance(data, dict) and data.get("status") == "success":
                    image_url = str(data.get("message", "")).strip()
                    if image_url:
                        return image_url
            except (aiohttp.ClientError, TimeoutError):
                pass

        # Public thedogapi breed_ids search returns unrelated breeds; use catalog image.
        catalog_url = _BREED_ID_TO_IMAGE_URL.get(breed_id)
        if catalog_url:
            return catalog_url
        return None

    try:
        data = await _fetch_json(session, DOG_CEO_RANDOM_URL)
        if isinstance(data, dict) and data.get("status") == "success":
            image_url = str(data.get("message", "")).strip()
            if image_url:
                return image_url
    except (aiohttp.ClientError, TimeoutError):
        pass

    try:
        data = await _fetch_json(session, THEDOGAPI_SEARCH_URL, params={"limit": "1"})
        if isinstance(data, list) and data:
            image_url = str(data[0].get("url", "")).strip()
            if image_url:
                return image_url
    except (aiohttp.ClientError, TimeoutError):
        pass

    try:
        data = await _fetch_json(session, RANDOM_DOG_URL)
        if isinstance(data, dict):
            image_url = str(data.get("url", "")).strip()
            if image_url and not image_url.lower().endswith(".mp4"):
                return image_url
    except (aiohttp.ClientError, TimeoutError):
        pass

    return None


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="8ball",
        description="Ask the magic 8-ball a question.",
)
    @app_commands.describe(question="Your yes/no style question")
    async def eight_ball(self, interaction: discord.Interaction, question: str) -> None:
        if not await _check_fun_enabled(interaction):
            return
        responses = [
            f"r{i:02d}" for i in range(1, 21)
        ]
        key_suffix = random.choice(responses)
        defaults = {
            "r01": "It is certain.", "r02": "Without a doubt.", "r03": "You may rely on it.",
            "r04": "Yes, definitely.", "r05": "It is decidedly so.", "r06": "As I see it, yes.",
            "r07": "Most likely.", "r08": "Outlook good.", "r09": "Yes.", "r10": "Signs point to yes.",
            "r11": "Reply hazy, try again.", "r12": "Ask again later.", "r13": "Better not tell you now.",
            "r14": "Cannot predict now.", "r15": "Concentrate and ask again.", "r16": "Don't count on it.",
            "r17": "My reply is no.", "r18": "My sources say no.", "r19": "Outlook not so good.",
            "r20": "Very doubtful.",
        }
        answer = await t(
            interaction.user.id,
            f"fun.eightball.{key_suffix}",
            default=defaults[key_suffix],
        )
        fmt = await t(
            interaction.user.id,
            "fun.eightball.format",
            default="🎱 **Question:** {question}\n**Answer:** {answer}",
            question=question,
            answer=answer,
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            fmt,
            question=question,
            answer=answer,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="bet",
        description="Place a bet with another user.",
)
    @app_commands.describe(member="User to bet with", bet="What you want to bet")
    async def bet(self, interaction: discord.Interaction, member: discord.Member, bet: str) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "{user} bet {amount} on {result}!",
            user=interaction.user.mention,
            amount=bet,
            result=member.mention,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="flipcoin",
        description="Flip a coin, optionally with a prize.",
)
    @app_commands.describe(prize="Prize to win")
    async def flipcoin(self, interaction: discord.Interaction, prize: Optional[str] = None) -> None:
        if not await _check_fun_enabled(interaction):
            return
        result_key = random.choice(["heads", "tails"])
        result = _fun_text_sync(
            interaction.user.id,
            f"flipcoin.{result_key}",
            default="Heads" if result_key == "heads" else "Tails",
        )
        if prize:
            msg = get_command_response_for_interaction(
                interaction,
                "success_with_prize",
                "🪙 The coin landed on **{result}**! {user} wins {prize}!",
                result=result,
                user=interaction.user.mention,
                prize=prize,
            )
            await interaction.response.send_message(msg)
        else:
            msg = get_command_response_for_interaction(
                interaction,
                "success",
                "🪙 The coin landed on **{result}**!",
                result=result,
            )
            await interaction.response.send_message(msg)

    @app_commands.command(
        name="hug",
        description="Give someone a hug.",
)
    @app_commands.describe(member="User to hug")
    async def hug(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🤗 {user} gives {member} a big hug!",
            user=interaction.user.mention,
            member=member.mention,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="kiss",
        description="Kiss someone.",
)
    @app_commands.describe(member="User to kiss")
    async def kiss(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "{user} kissed {member}!",
            user=interaction.user.mention,
            member=member.mention,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="lovecalc",
        description="Calculate love compatibility between two users.",
)
    @app_commands.describe(member1="First user", member2="Second user")
    async def lovecalc(
        self,
        interaction: discord.Interaction,
        member1: discord.Member,
        member2: discord.Member,
    ) -> None:
        if not await _check_fun_enabled(interaction):
            return
        score = random.randint(0, 100)
        hearts = "❤️" * (score // 10)
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "Love compatibility between {user1} and {user2}: **{percentage}%** {hearts}",
            user1=member1.mention,
            user2=member2.mention,
            percentage=str(score),
            hearts=hearts,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="truth",
        description="Ask someone a truth question.",
)
    @app_commands.describe(member="User to ask", question="Truth question")
    async def truth(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        question: str,
    ) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "{user} asked {member} a truth question: **{question}**",
            user=interaction.user.mention,
            member=member.mention,
            question=question,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="dare",
        description="Give someone a dare challenge.",
)
    @app_commands.describe(member="User to dare", challenge="Dare challenge")
    async def dare(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        challenge: str,
    ) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "{user} dared {member}: **{dare}**",
            user=interaction.user.mention,
            member=member.mention,
            dare=challenge,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="dog",
        description="Get a picture of a dog (optionally by breed)",
)
    @app_commands.describe(breed="Optional dog breed (e.g., pug, husky)")
    async def dog(self, interaction: discord.Interaction, breed: Optional[str] = None) -> None:
        if not await _check_fun_enabled(interaction):
            return
        await interaction.response.defer()
        try:
            async with _http_session(self.bot) as session:
                image_url = await _fetch_dog_image_url(session, breed)
        except (aiohttp.ClientError, TimeoutError):
            image_url = None

        if image_url:
            breed_text = f" ({breed})" if breed else ""
            msg = get_command_response_for_interaction(
                interaction,
                "success",
                "Here's a dog{breed}. {url}",
                breed=breed_text,
                url=image_url,
            )
            await interaction.followup.send(msg)
            return

        if breed:
            error_key = "dog_error"
            error_default = "❌ Breed not found or error getting dog image."
        else:
            error_key = "dog_unavailable"
            error_default = "❌ Dog image services are temporarily unavailable. Try again later."
        await interaction.followup.send(
            _fun_text_sync(interaction.user.id, error_key, default=error_default)
        )

    @app_commands.command(
        name="cat",
        description="Get a picture of a random cat",
)
    async def cat(self, interaction: discord.Interaction) -> None:
        if not await _check_fun_enabled(interaction):
            return
        await interaction.response.defer()
        url = "https://api.thecatapi.com/v1/images/search"
        async with _http_session(self.bot) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data:
                    msg = get_command_response_for_interaction(
                        interaction,
                        "success",
                        "Cat picture delivered. {url}",
                        url=data[0]["url"],
                    )
                    await interaction.followup.send(msg)
                else:
                    await interaction.followup.send(
                        _fun_text_sync(
                            interaction.user.id,
                            "cat_error",
                            default="❌ Could not get a cat image.",
                        )
                    )

    @app_commands.command(
        name="petpet",
        description="Generate a petpet GIF of a user's avatar",
)
    @app_commands.describe(member="User to petpet")
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: i.user.id)
    async def petpet_cmd(
        self,
        interaction: discord.Interaction,
        member: Optional[discord.Member] = None,
    ) -> None:
        if not await _check_fun_enabled(interaction):
            return
        try:
            from petpetgif import petpet
        except ImportError:
            await interaction.response.send_message(
                _fun_text_sync(
                    interaction.user.id,
                    "petpet_missing_dependency",
                    default="❌ Petpet is not installed. Install with: pip install petpetgif",
                ),
                ephemeral=True,
            )
            return

        member = member or interaction.user
        avatar_url = member.display_avatar.replace(size=256).url
        await interaction.response.defer()

        async with anti_abuse.heavy_task_slot():
            async with _http_session(self.bot) as session:
                async with session.get(avatar_url) as r:
                    img_bytes = await r.read()

            buf_in = io.BytesIO(img_bytes)
            buf_out = io.BytesIO()
            petpet.make(buf_in, buf_out)
            buf_out.seek(0)

        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "Petpet GIF created for {member}.",
            member=member.mention,
        )
        await interaction.followup.send(msg, file=discord.File(buf_out, filename="petpet.gif"))

    @app_commands.command(
        name="ak47",
        description="Send a random AK-47 gif",
)
    async def ak47(self, interaction: discord.Interaction) -> None:
        if not await _check_fun_enabled(interaction):
            return
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "AK-47 gif sent. {url}",
            url="https://giphy.com/gifs/cat-gun-thug-GaqnjVbSLs2uA",
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="uwuify",
        description="Convert text to uwu-style",
)
    @app_commands.describe(text="Text to uwuify")
    async def uwuify_cmd(self, interaction: discord.Interaction, text: str) -> None:
        if not await _check_fun_enabled(interaction):
            return
        try:
            from uwuify import uwu

            uwu_text = uwu(text)
            msg = get_command_response_for_interaction(
                interaction,
                "success",
                "Uwuified text: {text}",
                text=uwu_text,
            )
            await interaction.response.send_message(msg)
        except ImportError:
            await interaction.response.send_message(
                _fun_text_sync(
                    interaction.user.id,
                    "uwuify_missing_dependency",
                    default="❌ uwuify is not installed. Install with: pip install uwuify",
                ),
                ephemeral=True,
            )

    @app_commands.command(
        name="giftnuke",
        description="Send a gift... surprise! 🎁",
)
    @app_commands.describe(member="The target of your gift")
    async def nuke(self, interaction: discord.Interaction, member: discord.Member) -> None:
        if not await _check_fun_enabled(interaction):
            return
        url = "https://giphy.com/gifs/explosion-bomb-mushroom-X92pmIty2ZJp6"
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "Surprise! 🎁 {user} gave a gift to {member}! {url}",
            user=interaction.user.mention,
            member=member.mention,
            url=url,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="roast",
        description="Send a random roast",
)
    async def roast(self, interaction: discord.Interaction) -> None:
        if not await _check_fun_enabled(interaction):
            return
        roast_keys = [f"r{i:02d}" for i in range(1, 7)]
        roast_defaults = {
            "r01": "You're as bright as a black hole, and twice as dense.",
            "r02": "You have something on your chin… no, the third one down.",
            "r03": "You're the reason the gene pool needs a lifeguard.",
            "r04": "You bring everyone so much joy… when you leave the room.",
            "r05": "You have the perfect face for radio.",
            "r06": "You're like a cloud. When you disappear, it's a beautiful day.",
        }
        key = random.choice(roast_keys)
        roast = _fun_text_sync(
            interaction.user.id,
            f"roast.{key}",
            default=roast_defaults[key],
        )
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "Roast: **{roast}**",
            roast=roast,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(
        name="abracadaberamotherafu",
        description="💥 Casts a mighty spell of a BIG TANK gun from Toefingers tank on a tank!",
)
    async def abracadaberamotherafu(self, interaction: discord.Interaction) -> None:
        if not await _check_fun_enabled(interaction):
            return
        gif_url = "https://i.imgur.com/gXB0LAh.gif"
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🪄 **ABRACADABERA MOTHERAFU—**\n{user} just nuked a tank into the next dimension! 💥🚓🔥\n{url}",
            user=interaction.user.mention,
            url=gif_url,
        )
        await interaction.response.send_message(msg)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FunCog(bot))
