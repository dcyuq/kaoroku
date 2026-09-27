import discord
from discord import app_commands
from discord.ext import commands

import embeds
import kaolog as klog


class kaosendm(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_app_command_error(self, interaction, error):
        msg = (
            "you don't have permission to use that."
            if isinstance(error, app_commands.MissingPermissions)
            else "something broke on my end."
        )
        if interaction.response.is_done():
            await interaction.followup.send(embed=embeds.error(msg), ephemeral=True)
        else:
            await interaction.response.send_message(
                embed=embeds.error(msg), ephemeral=True
            )

    @app_commands.command(
        name="kaosendm", description="Send a direct message to a user as the bot"
    )
    @app_commands.describe(
        user="Who to send the DM to",
        content="What you want the bot to send",
        image="An image or file to attach",
        file2="Another file",
        file3="Another file",
        file4="Another file",
        file5="Another file",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def send_dm(
        self,
        interaction,
        user: discord.Member,
        content: str = None,
        image: discord.Attachment = None,
        file2: discord.Attachment = None,
        file3: discord.Attachment = None,
        file4: discord.Attachment = None,
        file5: discord.Attachment = None,
    ):
        text = (content or "").strip()
        attachments = [image, file2, file3, file4, file5]

        if user.bot:
            return await interaction.response.send_message(
                embed=embeds.error("you can't dm a bot."), ephemeral=True
            )
        if not text and not any(attachments):
            return await interaction.response.send_message(
                embed=embeds.error("give me some text or a file to send."),
                ephemeral=True,
            )
        if len(text) > klog.MAX_CHARS:
            return await interaction.response.send_message(
                embed=embeds.error(f"keep it under {klog.MAX_CHARS} characters."),
                ephemeral=True,
            )

        await interaction.response.defer(ephemeral=True)

        pictures, problem = await klog.collect(attachments)
        if problem:
            return await interaction.followup.send(
                embed=embeds.error(problem), ephemeral=True
            )

        files, _ = klog.payload(pictures)
        try:
            await user.send(content=text or None, files=files or None)
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=embeds.error(
                    f"i couldn't dm {user.mention}. their dms are closed or i'm blocked."
                ),
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                embed=embeds.error(f"failed to dm {user.mention}, api error."),
                ephemeral=True,
            )

        await interaction.followup.send(
            embed=embeds.notice(f"sent a dm to {user.mention}."), ephemeral=True
        )

        if klog.get_log_channel_id(interaction.guild.id):
            log_files, log_ref = klog.payload(pictures)
            log = klog.log_embed(
                "kaosendm (dm)", interaction.user, user.mention, text, pictures
            )
            if log_ref:
                log.set_image(url=log_ref)
            await klog.send_log(interaction.guild, log, files=log_files)


async def setup(bot):
    await bot.add_cog(kaosendm(bot))