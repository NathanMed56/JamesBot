import asyncio
from dataclasses import dataclass, field
from typing import Optional, Dict, List

import discord
from discord import app_commands, FFmpegPCMAudio
from discord.ext import commands, tasks
from discord.ui import View
from yt_dlp import YoutubeDL
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials


# ------------------------
# CONFIG
# ------------------------

# Guild where slash commands are registered (if you want global, remove @guilds decorator)
GUILD_ID = 1435711020680347688

# Spotify API credentials
SPOTIFY_CLIENT_ID = "9966109f1afc4e8fb45113808b1a6dc7"
SPOTIFY_CLIENT_SECRET = "1b7cfbbd75ab4e70a6d1b26a053859fd"

# YT-DLP options
YDL_OPTIONS = {
    "format": "ba[ext=webm][acodec=opus]/ba/best",
    "noplaylist": False,
    "quiet": True,
    "no_warnings": True,
    "cachedir": False,
    "extract_flat": False,
    "cookiefile": "cookies.txt",
}

# FFMPEG options
FFMPEG_OPTIONS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

# Path to ffmpeg on your VPS
FFMPEG_EXECUTABLE = "/usr/bin/ffmpeg"  # change if different on your system


# ------------------------
# DATA STRUCTURES
# ------------------------

@dataclass
class Track:
    url: str                 # Original URL (YouTube page)
    source_url: str          # Direct audio stream URL from yt-dlp
    title: str
    thumbnail: Optional[str]
    duration: Optional[int]  # seconds


@dataclass
class GuildMusicState:
    queue: List[Track] = field(default_factory=list)
    current: Optional[Track] = None
    volume: float = 1.0
    text_channel: Optional[discord.abc.Messageable] = None
    now_playing_msg: Optional[discord.Message] = None
    idle_time: int = 0
    voice_channel_id: Optional[int] = None


# ------------------------
# MUSIC COG
# ------------------------

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Spotify client
        self.sp = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=SPOTIFY_CLIENT_ID,
                client_secret=SPOTIFY_CLIENT_SECRET,
            )
        )

        # Per-guild state
        self.states: Dict[int, GuildMusicState] = {}

        # Idle disconnect loop
        self.idle_checker.start()

    def cog_unload(self):
        self.idle_checker.cancel()

    # ------------
    # State helper
    # ------------

    def get_state(self, guild: discord.Guild) -> GuildMusicState:
        state = self.states.get(guild.id)
        if state is None:
            state = GuildMusicState()
            self.states[guild.id] = state
        return state

    # ------------
    # Utilities
    # ------------

    def format_duration(self, seconds: Optional[int]) -> str:
        if seconds is None:
            return "Unknown"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02}:{s:02}"
        return f"{m}:{s:02}"

    async def search_youtube(self, query: str) -> Optional[str]:
        ydl_opts = {"format": "bestaudio", "noplaylist": True, "quiet": True}
        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(f"ytsearch:{query}", download=False)["entries"][0]
                return info["webpage_url"]
            except Exception:
                return None

    def build_track_from_info(self, info: dict, url: str) -> Track:
        return Track(
            url=url,
            source_url=info["url"],
            title=info.get("title", "Unknown"),
            thumbnail=info.get("thumbnail"),
            duration=info.get("duration"),
        )

    # ------------------------
    # CORE PLAYBACK
    # ------------------------

    async def ensure_voice(self, guild: discord.Guild, state: GuildMusicState) -> Optional[discord.VoiceClient]:
        """Ensure the bot is connected to a voice channel for this guild."""
        vc = guild.voice_client

        # Try existing connection
        if vc and vc.is_connected():
            return vc

        # Need to connect
        if state.voice_channel_id is None:
            # No record of which channel to use
            if state.text_channel:
                await state.text_channel.send("I don't know which voice channel to join. Use `/play` while in a voice channel.")
            return None

        voice_channel = guild.get_channel(state.voice_channel_id)
        if not isinstance(voice_channel, discord.VoiceChannel):
            if state.text_channel:
                await state.text_channel.send("Saved voice channel is invalid. Join a voice channel and use `/play` again.")
            return None

        try:
            vc = await voice_channel.connect()
            # Short delay to let voice WS fully initialise
            await asyncio.sleep(0.25)
            return vc
        except Exception as e:
            if state.text_channel:
                await state.text_channel.send(f"❌ Failed to connect to voice channel: `{e}`")
            return None

    async def start_playback_if_needed(self, guild: discord.Guild):
        """Start playback if nothing is currently playing."""
        state = self.get_state(guild)
        vc = guild.voice_client

        if state.current is None and state.queue:
            await self.play_next_track(guild)
        elif not vc or not vc.is_playing():
            # In case voice client died mid-track
            await self.play_next_track(guild)

    async def play_next_track(self, guild: discord.Guild):
        """Pop next track from queue and play it."""
        state = self.get_state(guild)

        if not state.queue:
            state.current = None
            state.idle_time = 0
            # Optionally clear now playing message
            return

        # Get next URL and resolve audio stream with yt-dlp
        next_url = state.queue[0].url

        with YoutubeDL(YDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(next_url, download=False)
            except Exception as e:
                if state.text_channel:
                    await state.text_channel.send(f"Failed to fetch audio: {e}")
                # Drop this track and try next
                state.queue.pop(0)
                return await self.play_next_track(guild)

        track = self.build_track_from_info(info, next_url)
        state.current = track

        # Ensure voice connection
        vc = await self.ensure_voice(guild, state)
        if not vc:
            # Can't connect, drop track
            state.queue.pop(0)
            state.current = None
            return

        # Prepare source
        source = FFmpegPCMAudio(
            track.source_url,
            executable=FFMPEG_EXECUTABLE,
            **FFMPEG_OPTIONS,
        )

        # Reset idle timer
        state.idle_time = 0

        # Define callback for when track finishes
        def after_playback(error: Optional[Exception]):
            if error:
                print(f"[Music] Playback error in guild {guild.id}: {error}")
            # This runs in a different thread — use run_coroutine_threadsafe
            fut = asyncio.run_coroutine_threadsafe(self.on_track_end(guild.id), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"[Music] Error in on_track_end: {e}")

        # Start playing
        try:
            vc.play(
                discord.PCMVolumeTransformer(source, volume=state.volume),
                after=after_playback,
            )
        except Exception as e:
            if state.text_channel:
                asyncio.create_task(state.text_channel.send(f"❌ Error playing audio: `{e}`"))
            return

        # Send now playing embed
        if state.text_channel:
            embed = discord.Embed(
                title="Now Playing",
                description=f"[{track.title}]({track.url})",
                color=discord.Color.blurple(),
            )
            if track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)
            msg = await state.text_channel.send(embed=embed)
            state.now_playing_msg = msg

        # Start progress updater loop
        asyncio.create_task(self.update_progress(guild.id))

    async def on_track_end(self, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        state = self.get_state(guild)
        # Remove the finished track from queue
        if state.queue:
            state.queue.pop(0)

        # Play next if queue not empty
        if state.queue:
            await self.play_next_track(guild)
        else:
            state.current = None
            state.idle_time = 0

    async def update_progress(self, guild_id: int):
        """Update "Now Playing" message with a text progress bar."""
        guild = self.bot.get_guild(guild_id)
        if not guild:
            return

        state = self.get_state(guild)
        msg = state.now_playing_msg
        track = state.current

        if not msg or not track:
            return

        vc = guild.voice_client
        if not vc:
            return

        duration = track.duration
        start_time = asyncio.get_event_loop().time()

        while True:
            await asyncio.sleep(5)

            vc = guild.voice_client
            if not vc or not vc.is_connected() or not (vc.is_playing() or vc.is_paused()):
                break

            elapsed = asyncio.get_event_loop().time() - start_time

            if duration:
                bar_len = 20
                filled_len = min(int((elapsed / duration) * bar_len), bar_len)
                bar = "█" * filled_len + "─" * (bar_len - filled_len)
                progress_text = f"[{bar}] {self.format_duration(elapsed)} / {self.format_duration(duration)}"
            else:
                progress_text = f"Elapsed: {self.format_duration(elapsed)}"

            embed = discord.Embed(
                title="Now Playing",
                description=f"[{track.title}]({track.url})",
                color=discord.Color.blurple(),
            )
            if track.thumbnail:
                embed.set_thumbnail(url=track.thumbnail)
            embed.add_field(name="Progress", value=progress_text, inline=False)

            try:
                await msg.edit(embed=embed)
            except Exception:
                break

    # ------------------------
    # COMMANDS
    # ------------------------

    @app_commands.command(name="play", description="Play a YouTube or Spotify track/playlist")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(url="YouTube or Spotify URL")
    async def play(self, interaction: discord.Interaction, url: str):
        await interaction.response.defer(thinking=True)

        # Require user in voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("You must be in a voice channel to use `/play`.")

        guild = interaction.guild
        state = self.get_state(guild)

        # Remember text channel & voice channel
        state.text_channel = interaction.channel
        state.voice_channel_id = interaction.user.voice.channel.id

        urls_to_add: List[str] = []

        # Handle Spotify
        if "open.spotify.com" in url:
            try:
                if "track" in url:
                    track = self.sp.track(url)
                    query = f"{track['name']} {track['artists'][0]['name']}"
                    yt_url = await self.search_youtube(query)
                    if yt_url:
                        urls_to_add.append(yt_url)

                elif "playlist" in url:
                    results = self.sp.playlist_items(url)
                    for item in results["items"]:
                        t = item.get("track")
                        if not t:
                            continue
                        query = f"{t['name']} {t['artists'][0]['name']}"
                        yt_url = await self.search_youtube(query)
                        if yt_url:
                            urls_to_add.append(yt_url)
            except Exception as e:
                return await interaction.followup.send(f"Failed to process Spotify URL: {e}")
        else:
            urls_to_add.append(url)

        if not urls_to_add:
            return await interaction.followup.send("No valid songs could be added.")

        # Resolve basic Track entries (with dummy source_url, we fill real one when playing)
        for u in urls_to_add:
            # For queue display we only need URL and maybe title (we'll show URL if unknown)
            state.queue.append(Track(url=u, source_url="", title=u, thumbnail=None, duration=None))

        await interaction.followup.send(f"Added {len(urls_to_add)} song(s) to the queue.")

        await self.start_playback_if_needed(guild)

    # ------------------------
    # SEARCH COMMAND
    # ------------------------

    @app_commands.command(name="search", description="Search YouTube and choose a song")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(query="Search query")
    async def search(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True)

        # Require user in voice channel
        if not interaction.user.voice or not interaction.user.voice.channel:
            return await interaction.followup.send("You must be in a voice channel to use `/search`.")

        guild = interaction.guild
        state = self.get_state(guild)
        state.text_channel = interaction.channel
        state.voice_channel_id = interaction.user.voice.channel.id

        page_size = 5

        async def get_results(q: str):
            ydl_opts = {
                "format": "bestaudio",
                "noplaylist": True,
                "quiet": True,
                "extract_flat": True,
            }
            with YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(f"ytsearch{page_size}:{q}", download=False)
                    return info["entries"]
                except Exception as e:
                    print(f"yt-dlp search error: {e}")
                    return []

        def make_embed(results):
            embed = discord.Embed(
                title=f"Search results for: {query}",
                color=discord.Color.blue(),
            )
            if not results:
                embed.description = "No results."
            for i, r in enumerate(results):
                title = r.get("title", "Unknown title")
                url = r.get("url") or f"https://www.youtube.com/watch?v={r.get('id', '')}"
                embed.add_field(
                    name=f"{i+1}. {title}",
                    value=f"[Link]({url})",
                    inline=False,
                )
            return embed

        class SearchView(View):
            def __init__(self, results):
                super().__init__(timeout=60)
                self.results = results
                self.choice: Optional[int] = None

            async def select_song(self, index: int, interaction_btn: discord.Interaction):
                if len(self.results) > index:
                    self.choice = index
                    selected_title = self.results[index].get("title", "Unknown title")
                    await interaction_btn.response.edit_message(
                        content=f"Selected: {selected_title}",
                        embed=None,
                        view=None,
                    )
                    self.stop()

            @discord.ui.button(label="1", style=discord.ButtonStyle.primary)
            async def one(self, interaction_btn, _):
                await self.select_song(0, interaction_btn)

            @discord.ui.button(label="2", style=discord.ButtonStyle.primary)
            async def two(self, interaction_btn, _):
                await self.select_song(1, interaction_btn)

            @discord.ui.button(label="3", style=discord.ButtonStyle.primary)
            async def three(self, interaction_btn, _):
                await self.select_song(2, interaction_btn)

            @discord.ui.button(label="4", style=discord.ButtonStyle.primary)
            async def four(self, interaction_btn, _):
                await self.select_song(3, interaction_btn)

            @discord.ui.button(label="5", style=discord.ButtonStyle.primary)
            async def five(self, interaction_btn, _):
                await self.select_song(4, interaction_btn)

            @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
            async def cancel(self, interaction_btn, _):
                self.choice = None
                await interaction_btn.response.edit_message(
                    content="Search cancelled.",
                    embed=None,
                    view=None,
                )
                self.stop()

        results = await get_results(query)
        view = SearchView(results)
        await interaction.followup.send(embed=make_embed(results), view=view)
        await view.wait()

        if view.choice is None:
            return

        selected = results[view.choice]
        url = selected.get("url") or f"https://www.youtube.com/watch?v={selected.get('id', '')}"
        title = selected.get("title", url)

        state.queue.append(Track(url=url, source_url="", title=title, thumbnail=None, duration=None))
        await interaction.followup.send(f"Added to queue: {title}")

        await self.start_playback_if_needed(guild)

    # ------------------------
    # SIMPLE CONTROL COMMANDS
    # ------------------------

    @app_commands.command(name="skip", description="Skip current song")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def skip(self, interaction: discord.Interaction):
        guild = interaction.guild
        vc = guild.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await interaction.response.send_message("Skipped!")
        else:
            await interaction.response.send_message("Nothing is playing!")

    @app_commands.command(name="pause", description="Pause current song")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def pause(self, interaction: discord.Interaction):
        guild = interaction.guild
        vc = guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("Paused!")
        else:
            await interaction.response.send_message("Nothing is playing!")

    @app_commands.command(name="resume", description="Resume paused song")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def resume(self, interaction: discord.Interaction):
        guild = interaction.guild
        vc = guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("Resumed!")
        else:
            await interaction.response.send_message("Nothing is paused!")

    @app_commands.command(name="volume", description="Set volume 0–100%")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(percent="Volume percent")
    async def volume(self, interaction: discord.Interaction, percent: int):
        if not 0 <= percent <= 100:
            return await interaction.response.send_message("Volume must be between 0 and 100.")

        guild = interaction.guild
        state = self.get_state(guild)
        state.volume = percent / 100

        vc = guild.voice_client
        if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
            vc.source.volume = state.volume

        await interaction.response.send_message(f"Volume set to {percent}%.")

    @app_commands.command(name="remove", description="Remove a song from the queue")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    @app_commands.describe(position="Position in queue (starting at 1)")
    async def remove(self, interaction: discord.Interaction, position: int):
        guild = interaction.guild
        state = self.get_state(guild)
        queue = state.queue

        if 1 <= position <= len(queue):
            removed = queue.pop(position - 1)
            await interaction.response.send_message(f"Removed: {removed.title}")
        else:
            await interaction.response.send_message("Invalid position!")

    @app_commands.command(name="queue", description="Show the current queue")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def queue_cmd(self, interaction: discord.Interaction):
        guild = interaction.guild
        state = self.get_state(guild)
        queue = state.queue

        if not queue:
            return await interaction.response.send_message("Queue is empty!")

        embed = discord.Embed(title="Queue", color=discord.Color.green())
        for i, track in enumerate(queue, start=1):
            title = track.title or track.url
            embed.add_field(
                name=f"{i}.",
                value=f"[{title}]({track.url})",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nowplaying", description="Show currently playing song")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def nowplaying(self, interaction: discord.Interaction):
        guild = interaction.guild
        state = self.get_state(guild)
        track = state.current

        if not track:
            return await interaction.response.send_message("Nothing is playing!")

        embed = discord.Embed(
            title="Now Playing",
            description=f"[{track.title}]({track.url})",
            color=discord.Color.blurple(),
        )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        embed.add_field(
            name="Duration",
            value=self.format_duration(track.duration),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leave", description="Disconnect the bot and clear queue")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def leave(self, interaction: discord.Interaction):
        guild = interaction.guild
        state = self.get_state(guild)
        vc = guild.voice_client

        if vc:
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            await vc.disconnect()

        # Clear state
        state.queue.clear()
        state.current = None
        state.idle_time = 0
        state.now_playing_msg = None
        state.voice_channel_id = None

        await interaction.response.send_message("Disconnected and cleared the queue.")

    @app_commands.command(name="musichelp", description="Show all music commands")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def music_help(self, interaction: discord.Interaction):
        embed = discord.Embed(title="Music Bot Commands", color=discord.Color.gold())
        embed.add_field(name="/play <URL>", value="Play a YouTube/Spotify track or playlist", inline=False)
        embed.add_field(name="/search <query>", value="Search YouTube and pick a result", inline=False)
        embed.add_field(name="/queue", value="Show queue", inline=False)
        embed.add_field(name="/nowplaying", value="Show current song", inline=False)
        embed.add_field(name="/skip", value="Skip current song", inline=False)
        embed.add_field(name="/pause", value="Pause playback", inline=False)
        embed.add_field(name="/resume", value="Resume playback", inline=False)
        embed.add_field(name="/volume <0-100>", value="Set playback volume", inline=False)
        embed.add_field(name="/remove <pos>", value="Remove a song from queue", inline=False)
        embed.add_field(name="/leave", value="Disconnect bot and clear queue", inline=False)
        await interaction.response.send_message(embed=embed)

    # ------------------------
    # IDLE DISCONNECT
    # ------------------------

    @tasks.loop(seconds=10)
    async def idle_checker(self):
        """Disconnect from voice if idle for 120 seconds."""
        for guild in list(self.bot.guilds):
            state = self.states.get(guild.id)
            if not state:
                continue

            vc = guild.voice_client
            if vc and vc.is_connected():
                if not vc.is_playing() and not vc.is_paused():
                    state.idle_time += 10
                    if state.idle_time >= 120:
                        try:
                            await vc.disconnect()
                        except Exception:
                            pass
                        state.idle_time = 0
                        state.queue.clear()
                        state.current = None
                        state.now_playing_msg = None
                else:
                    state.idle_time = 0

    @idle_checker.before_loop
    async def before_idle_checker(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
