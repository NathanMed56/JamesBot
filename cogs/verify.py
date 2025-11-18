import discord
from discord import app_commands
from discord.ext import commands

GUILD_ID = 1435711020680347688
ADMIN_ROLE_ID = 1440343274350186657
REMOVE_ROLE_ID = 1436330694778552330
ADD_ROLE_ID = 1436041906789290154
LOG_CHANNEL_ID = 1439889638826180648

class Verify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # REQUIRED IN 2.3+ — registers the slash command
        self.bot.tree.add_command(self.verify, guild=discord.Object(id=GUILD_ID))

    @app_commands.command(name="verify", description="Verify a user")
    @app_commands.guild_only()
    async def verify(self, interaction: discord.Interaction, member: discord.Member):
        # Check admin role
        if ADMIN_ROLE_ID not in [r.id for r in interaction.user.roles]:
            return await interaction.response.send_message(
                "❌ You do not have permission to use this command.",
                ephemeral=True
            )

        # Do the role change
        remove_role = interaction.guild.get_role(REMOVE_ROLE_ID)
        add_role = interaction.guild.get_role(ADD_ROLE_ID)

        if remove_role in member.roles:
            await member.remove_roles(remove_role)
        
        await member.add_roles(add_role)

        await interaction.response.send_message(
            f"✅ {member.mention} has been verified."
        )

        # Log the verification
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(
                f"✅ **Verification**\nUser: {member.mention}\nVerified By: {interaction.user.mention}"
            )

async def setup(bot):
    await bot.add_cog(Verify(bot))
