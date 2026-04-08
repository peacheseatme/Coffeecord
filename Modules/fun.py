"""
Fun commands: 8ball, bet, flipcoin, hug, kiss, lovecalc, truth, dare,
dog, cat, petpet, ak47, uwuify, nuke, roast, abracadaberamotherafu.
"""

import io
import random
from contextlib import asynccontextmanager
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from . import anti_abuse
from .module_registry import is_module_enabled
from .themes import get_command_response_for_interaction

__module_display_name__ = "Fun Commands"
__module_description__ = "8ball, flipcoin, hug, kiss, dog, cat, roast, and other fun commands."
__module_category__ = "engagement"


async def _check_fun_enabled(interaction: discord.Interaction) -> bool:
    """Return False if module disabled; sends message and returns False."""
    if interaction.guild is None:
        return True
    if not await is_module_enabled(interaction.guild.id, "fun"):
        await interaction.response.send_message(
            "This module is currently disabled. An admin can enable it with /modules.",
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


class FunCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="8ball", description="Ask the magic 8-ball a question.")
    @app_commands.describe(question="Your yes/no style question")
    async def eight_ball(self, interaction: discord.Interaction, question: str) -> None:
        if not await _check_fun_enabled(interaction):
            return
        responses = [
            "It is certain.",
            "Without a doubt.",
            "You may rely on it.",
            "Yes, definitely.",
            "It is decidedly so.",
            "As I see it, yes.",
            "Most likely.",
            "Outlook good.",
            "Yes.",
            "Signs point to yes.",
            "Reply hazy, try again.",
            "Ask again later.",
            "Better not tell you now.",
            "Cannot predict now.",
            "Concentrate and ask again.",
            "Don't count on it.",
            "My reply is no.",
            "My sources say no.",
            "Outlook not so good.",
            "Very doubtful.",
        ]
        answer = random.choice(responses)
        msg = get_command_response_for_interaction(
            interaction,
            "success",
            "🎱 **Question:** {question}\n**Answer:** {answer}",
            question=question,
            answer=answer,
        )
        await interaction.response.send_message(msg)

    @app_commands.command(name="bet", description="Place a bet with another user.")
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

    @app_commands.command(name="flipcoin", description="Flip a coin, optionally with a prize.")
    @app_commands.describe(prize="Prize to win")
    async def flipcoin(self, interaction: discord.Interaction, prize: Optional[str] = None) -> None:
        if not await _check_fun_enabled(interaction):
            return
        result = random.choice(["Heads", "Tails"])
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

    @app_commands.command(name="hug", description="Give someone a hug.")
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

    @app_commands.command(name="kiss", description="Kiss someone.")
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

    @app_commands.command(name="lovecalc", description="Calculate love compatibility between two users.")
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

    @app_commands.command(name="truth", description="Ask someone a truth question.")
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

    @app_commands.command(name="dare", description="Give someone a dare challenge.")
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

    @app_commands.command(name="dog", description="Get a picture of a dog (optionally by breed)")
    @app_commands.describe(breed="Optional dog breed (e.g., pug, husky)")
    async def dog(self, interaction: discord.Interaction, breed: Optional[str] = None) -> None:
        if not await _check_fun_enabled(interaction):
            return
        await interaction.response.defer()
        url = "https://dog.ceo/api/breeds/image/random"
        if breed:
            url = f"https://dog.ceo/api/breed/{breed.lower()}/images/random"
        async with _http_session(self.bot) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                if data.get("status") == "success":
                    breed_text = f" ({breed})" if breed else ""
                    msg = get_command_response_for_interaction(
                        interaction,
                        "success",
                        "Here's a dog{breed}. {url}",
                        breed=breed_text,
                        url=data["message"],
                    )
                    await interaction.followup.send(msg)
                else:
                    await interaction.followup.send("❌ Breed not found or error getting dog image.")

    @app_commands.command(name="cat", description="Get a picture of a random cat")
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
                    await interaction.followup.send("❌ Could not get a cat image.")

    @app_commands.command(name="petpet", description="Generate a petpet GIF of a user's avatar")
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
                "❌ Petpet is not installed. Install with: pip install petpetgif",
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

    @app_commands.command(name="ak47", description="Send a random AK-47 gif")
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

    @app_commands.command(name="uwuify", description="Convert text to uwu-style")
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
                "❌ uwuify is not installed. Install with: pip install uwuify",
                ephemeral=True,
            )

    @app_commands.command(name="giftnuke", description="Send a gift... surprise! 🎁")
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

    @app_commands.command(name="roast", description="Send a random roast")
    async def roast(self, interaction: discord.Interaction) -> None:
        if not await _check_fun_enabled(interaction):
            return
        roasts = [
            "You're as bright as a black hole, and twice as dense.",
            "You have something on your chin… no, the third one down.",
            "You're the reason the gene pool needs a lifeguard.",
            "You bring everyone so much joy… when you leave the room.",
            "You have the perfect face for radio.",
            "You're like a cloud. When you disappear, it's a beautiful day.",
        ]
        roast = random.choice(roasts)
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
