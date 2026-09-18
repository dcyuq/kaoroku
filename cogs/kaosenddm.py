import discord
from discord import app_commands
from discord.ext import commands


class kaosendm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kaosendm", description="Send a direct message to a user as the bot")
    @app_commands.describe(content="What you want the bot to send", user="Who to send the DM to")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def send_dm(self, interaction: discord.Interaction, user: discord.Member, content: str):
        try:
            await user.send(content)
            await interaction.response.send_message(f"Successfully sent a DM to {user.mention}", ephemeral=True)
            
        except discord.Forbidden:
            await interaction.response.send_message(f"I couldn't DM {user.mention}. They might have DMs disabled or blocked me.", ephemeral=True)
            
        except discord.HTTPException:
            await interaction.response.send_message(f"Failed to send the DM to {user.mention} due to an API error.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(kaosendm(bot))