import discord
from discord import app_commands
from discord.ext import commands

import embeds
import kaolog as klog


class kaosend(commands.Cog):
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

    @app_commands.command(name="kaosend", description="Send a message as the kao")
    @app_commands.describe(
        content="What you want the bot to send",
        channel="Where to send it",
        image="An image or file to attach",
        file2="Another file",
        file3="Another file",
        file4="Another file",
        file5="Another file",
    )
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.guild_only()
    async def send(
        self,
        interaction,
        content: str = None,
        channel: discord.TextChannel = None,
        image: discord.Attachment = None,
        file2: discord.Attachment = None,
        file3: discord.Attachment = None,
        file4: discord.Attachment = None,
        file5: discord.Attachment = None,
    ):
        target = channel or interaction.channel
        text = (content or "").strip()
        attachments = [image, file2, file3, file4, file5]

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

        perms = target.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.view_channel):
            return await interaction.response.send_message(
                embed=embeds.error(f"i can't send in {target.mention}."),
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
            sent = await target.send(content=text or None, files=files or None)
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=embeds.error(f"i can't send in {target.mention}."),
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.followup.send(
                embed=embeds.error("discord turned that message down."),
                ephemeral=True,
            )

        await interaction.followup.send(
            embed=embeds.notice(f"sent in {target.mention}. {sent.jump_url}"),
            ephemeral=True,
        )

        if klog.get_log_channel_id(interaction.guild.id):
            log_files, log_ref = klog.payload(pictures)
            log = klog.log_embed(
                "kaosend", interaction.user, target.mention, text, pictures
            )
            if log_ref:
                log.set_image(url=log_ref)
            await klog.send_log(interaction.guild, log, files=log_files)

    @app_commands.command(
        name="kaolog",
        description="Set where kaosend and kaosendm post their logs",
    )
    @app_commands.describe(channel="Log channel, leave empty to turn logging off")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def kaolog(self, interaction, channel: discord.TextChannel = None):
        if channel is None:
            klog.set_log_channel(interaction.guild.id, None)
            return await interaction.response.send_message(
                embed=embeds.notice("kao logging turned off."), ephemeral=True
            )
        klog.set_log_channel(interaction.guild.id, channel.id)
        await interaction.response.send_message(
            embed=embeds.notice(f"kao logs will go to {channel.mention}."),
            ephemeral=True,
        )


async def setup(bot):
    await bot.add_cog(kaosend(bot))