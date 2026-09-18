import discord
from discord import app_commands
from discord.ext import commands


class kaosend(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="kaosend", description="Send a message as the kao")
    @app_commands.describe(content="What you want the bot to send", channel="Where to send it")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def send(self, interaction: discord.Interaction, content: str, channel: discord.TextChannel = None):
        target = channel or interaction.channel
        perms = target.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.view_channel):
            return await interaction.response.send_message(f"I can't send in {target.mention}", ephemeral=True)
        await target.send(content)
        await interaction.response.send_message(f"Sent in {target.mention}", ephemeral=True)


async def setup(bot):
    await bot.add_cog(kaosend(bot))