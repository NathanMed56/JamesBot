import discord
from discord.ext import commands

class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # Just some debug info
        print("[misc] Cog ready")
        print("[misc] Slash commands in tree:", [c.name for c in self.bot.tree.get_commands()])

async def setup(bot):
    await bot.add_cog(Misc(bot))
