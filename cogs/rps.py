import discord
from discord.ext import commands
from discord import app_commands
import json, os

class RPS(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # Game storage
        self.rps_games = {}     # { (guild, p1, p2): {"choices": {uid: "Rock"}} }
        self.rps_stats = {}     # { user_id: {wins:0, losses:0, ties:0} }
        self.SAVE_FILE = "rps_stats.json"

        self.load_stats()

    # -----------------------------------
    # Save/Load Stats
    # -----------------------------------
    def save_stats(self):
        with open(self.SAVE_FILE, "w") as f:
            json.dump(self.rps_stats, f)

    def load_stats(self):
        if os.path.exists(self.SAVE_FILE):
            with open(self.SAVE_FILE, "r") as f:
                try:
                    self.rps_stats = json.load(f)
                except:
                    self.rps_stats = {}

    # -----------------------------------
    def get_key(self, guild_id, p1, p2):
        return (guild_id, min(p1, p2), max(p1, p2))

    def update_stats(self, winner_id=None, loser_id=None, tie_ids=None):
        if tie_ids:
            for uid in tie_ids:
                self.rps_stats.setdefault(uid, {"wins": 0, "losses": 0, "ties": 0})
                self.rps_stats[uid]["ties"] += 1
        else:
            for uid, key in [(winner_id, "wins"), (loser_id, "losses")]:
                self.rps_stats.setdefault(uid, {"wins": 0, "losses": 0, "ties": 0})
                self.rps_stats[uid][key] += 1

        self.save_stats()

    # -----------------------------------
    # /rpscancel
    # -----------------------------------
    @app_commands.command(name="rpscancel", description="Cancel your active RPS game with someone.")
    async def rpscancel(self, interaction: discord.Interaction, opponent: discord.Member):
        key = self.get_key(interaction.guild.id, interaction.user.id, opponent.id)
        if key in self.rps_games:
            self.rps_games.pop(key)
            await interaction.response.send_message("Game cancelled.", ephemeral=True)
        else:
            await interaction.response.send_message("No active game with that user.", ephemeral=True)

    # -----------------------------------
    # /rps
    # -----------------------------------
    @app_commands.command(name="rps", description="Challenge someone to Rock Paper Scissors")
    async def rps(self, interaction: discord.Interaction, opponent: discord.Member):

        challenger = interaction.user
        guild = interaction.guild
        gid = guild.id

        if challenger.id == opponent.id:
            return await interaction.response.send_message("You cannot challenge yourself.", ephemeral=True)

        key = self.get_key(gid, challenger.id, opponent.id)

        if key in self.rps_games:
            return await interaction.response.send_message("A game between you two already exists!", ephemeral=True)

        # Create game
        self.rps_games[key] = {"choices": {}}

        emoji_map = {"Rock": "🪨", "Paper": "📄", "Scissors": "✂️"}
        options = ["Rock", "Paper", "Scissors"]

        # ---------------------------------------
        # Challenger Choice UI
        # ---------------------------------------
        class ChallengerView(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=300)
                self.cog = cog

            async def choose(self, interaction_btn: discord.Interaction, choice: str):
                self.cog.rps_games[key]["choices"][challenger.id] = choice
                await interaction_btn.response.send_message(
                    f"You chose {emoji_map[choice]}",
                    ephemeral=True
                )
                self.stop()

        cv = ChallengerView(self)

        for opt in options:
            btn = discord.ui.Button(label=emoji_map[opt], style=discord.ButtonStyle.primary)

            async def callback(ibtn: discord.Interaction, choice=opt):
                if ibtn.user.id != challenger.id:
                    return await ibtn.response.send_message("This button isn't for you.", ephemeral=True)
                await cv.choose(ibtn, choice)

            btn.callback = callback
            cv.add_item(btn)

        await interaction.response.send_message(
            f"{challenger.mention}, choose your move:",
            view=cv,
            ephemeral=True
        )

        # ---------------------------------------
        # Opponent Accept/Decline
        # ---------------------------------------
        class AcceptDecline(discord.ui.View):
            def __init__(self, cog):
                super().__init__(timeout=300)
                self.cog = cog
                self.message = None

            @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
            async def accept(self, i: discord.Interaction, _):
                if i.user.id != opponent.id:
                    return await i.response.send_message("Not your challenge.", ephemeral=True)

                for c in self.children:
                    c.disabled = True
                await self.message.edit(view=self)

                await i.response.send_message("Challenge accepted! Check your private message.", ephemeral=True)
                await send_opponent_choice(i)

            @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
            async def decline(self, i: discord.Interaction, _):
                if i.user.id != opponent.id:
                    return await i.response.send_message("Not your challenge.", ephemeral=True)

                self.cog.rps_games.pop(key, None)
                await i.response.send_message("Challenge declined.", ephemeral=True)

        adv = AcceptDecline(self)

        challenge_msg = await interaction.channel.send(
            f"{opponent.mention}, **{challenger.display_name}** challenged you to Rock Paper Scissors!",
            view=adv
        )
        adv.message = challenge_msg

        # ---------------------------------------
        # Opponent Choice UI
        # ---------------------------------------
        async def send_opponent_choice(response_interaction: discord.Interaction):

            class OpponentView(discord.ui.View):
                def __init__(self, cog):
                    super().__init__(timeout=300)
                    self.cog = cog
                    self.message = None

                async def choose(self, ibtn: discord.Interaction, choice: str):
                    self.cog.rps_games[key]["choices"][opponent.id] = choice

                    await ibtn.response.send_message(
                        f"You chose {emoji_map[choice]}",
                        ephemeral=True
                    )
                    self.stop()

                    # If both picked, decide result
                    choices = self.cog.rps_games[key]["choices"]
                    if len(choices) == 2:
                        p1_id, p2_id = key[1], key[2]
                        p1 = guild.get_member(p1_id)
                        p2 = guild.get_member(p2_id)

                        p1c = choices[p1_id]
                        p2c = choices[p2_id]

                        # Decide winner
                        if p1c == p2c:
                            result = f"It's a tie! You both picked {emoji_map[p1c]}"
                            self.cog.update_stats(tie_ids=[p1_id, p2_id])
                        elif (
                            (p1c == "Rock" and p2c == "Scissors")
                            or (p1c == "Paper" and p2c == "Rock")
                            or (p1c == "Scissors" and p2c == "Paper")
                        ):
                            result = f"{p1.mention} wins! {emoji_map[p1c]} beats {emoji_map[p2c]}"
                            self.cog.update_stats(winner_id=p1_id, loser_id=p2_id)
                        else:
                            result = f"{p2.mention} wins! {emoji_map[p2c]} beats {emoji_map[p1c]}"
                            self.cog.update_stats(winner_id=p2_id, loser_id=p1_id)

                        await response_interaction.channel.send(result)

                        self.cog.rps_games.pop(key, None)

            ov = OpponentView(self)

            for opt in options:
                btn = discord.ui.Button(label=emoji_map[opt], style=discord.ButtonStyle.primary)

                async def callback(ibtn: discord.Interaction, choice=opt):
                    if ibtn.user.id != opponent.id:
                        return await ibtn.response.send_message("Not your turn.", ephemeral=True)
                    await ov.choose(ibtn, choice)

                btn.callback = callback
                ov.add_item(btn)

            msg = await response_interaction.followup.send(
                f"{opponent.mention}, choose your move:",
                view=ov,
                ephemeral=True
            )
            ov.message = msg

    # -----------------------------------
    # /rpsstats
    # -----------------------------------
    @app_commands.command(name="rpsstats", description="View your or another player's RPS stats")
    async def rpsstats(self, interaction: discord.Interaction, member: discord.Member = None):
        user = member or interaction.user

        stats = self.rps_stats.get(user.id)
        if not stats:
            return await interaction.response.send_message(
                f"{user.display_name} has no recorded games.",
                ephemeral=True
            )

        embed = discord.Embed(
            title=f"🪨📄✂️ RPS Stats — {user.display_name}",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Wins", value=stats["wins"])
        embed.add_field(name="Losses", value=stats["losses"])
        embed.add_field(name="Ties", value=stats["ties"])

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -----------------------------------
    # /rpsleaderboard
    # -----------------------------------
    @app_commands.command(name="rpsleaderboard", description="Show the RPS leaderboard")
    async def rpsleaderboard(self, interaction: discord.Interaction):

        if not self.rps_stats:
            return await interaction.response.send_message("No games have been played yet.", ephemeral=True)

        sorted_players = sorted(
            self.rps_stats.items(),
            key=lambda x: (x[1]["wins"], x[1]["ties"]),
            reverse=True
        )

        embed = discord.Embed(title="🏆 Rock Paper Scissors Leaderboard")

        desc = ""
        for rank, (uid, stats) in enumerate(sorted_players, start=1):
            member = interaction.guild.get_member(uid)
            name = member.display_name if member else f"User {uid}"
            desc += f"**#{rank} {name}** — {stats['wins']}W / {stats['losses']}L / {stats['ties']}T\n"

        embed.description = desc
        await interaction.response.send_message(embed=embed)
		
		
    @app_commands.command(name="rpsreset", description="OWNER ONLY — Reset all Rock Paper Scissors stats")
    async def rpsreset(self, interaction: discord.Interaction):

    # Only the bot owner may use this
        app = await self.bot.application_info()
    
        if interaction.user.id != app.owner.id:
            return await interaction.response.send_message(
                "❌ You are **not authorised** to reset the leaderboard.",
                ephemeral=True
            )

    # Reset stats
        self.rps_stats = {}
        self.save_stats()

        await interaction.response.send_message(
            "🧹 **RPS leaderboard has been wiped.**",
            ephemeral=True
        )



async def setup(bot):
    await bot.add_cog(RPS(bot))
