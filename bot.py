import aiohttp
import asyncio
import socket
import discord

from discord.ext import commands
import os

GUILD_ID = 1435711020680347688

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.all()

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


async def load_all_cogs():
    for filename in os.listdir("./cogs"):
        if filename.endswith(".py") and filename != "__init__.py":
            ext = f"cogs.{filename[:-3]}"
            try:
                await bot.load_extension(ext)
                print(f"[bot] Loaded cog: {ext}")
            except Exception as e:
                print(f"[bot] Failed to load cog {ext}: {e}")


@bot.event
async def setup_hook():
    # load cogs before syncing
    await load_all_cogs()

    guild = discord.Object(id=GUILD_ID)

    # sync ONLY to this guild (guild-only commands = instant)
    synced = await bot.tree.sync(guild=guild)
    print(f"[bot] Synced {len(synced)} commands to guild {GUILD_ID}")
    for cmd in synced:
        print(f" - /{cmd.name}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("Guilds:", [g.name for g in bot.guilds])
    

bot.run(TOKEN)

