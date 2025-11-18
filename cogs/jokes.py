import discord
from discord.ext import commands
from discord import app_commands
import random

class Jokes(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="jamesjoke", description="Get a random James joke")
    async def jamesjoke(self, interaction: discord.Interaction):
        with open("jokes.txt", "r", encoding="utf-8") as f:
            jokes = [line.strip() for line in f if line.strip()]

        await interaction.response.send_message(random.choice(jokes))

async def setup(bot):
    await bot.add_cog(Jokes(bot))
