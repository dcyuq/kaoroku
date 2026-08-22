import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from prefixes import (
    DEFAULT_PREFIX,
    clear_prefix,
    is_custom,
    prefix_for,
    set_prefix,
    validate,
)

log = logging.getLogger(__name__)


def can_manage(member: discord.Member) -> bool:
    return (
        member.id == member.guild.owner_id
        or member.guild_permissions.administrator
    )


def owner_or_admin():
    async def predicate(ctx):
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        if can_manage(ctx.author):
            return True
        raise commands.MissingPermissions(["administrator"])

    return commands.check(predicate)


class Prefix(commands.Cog):
    """Set the prefix the bot answers to in this server."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(
                ctx, embeds.error("this command only works in a server.")
            )
            return

        if isinstance(error, commands.MissingPermissions):
            await embeds.send(
                ctx,
                embeds.error(
                    "only the server owner or an administrator can change "
                    "the prefix.",
                    title="Not allowed",
                ),
            )
            return

        if isinstance(error, commands.CommandOnCooldown):
            await embeds.send(
                ctx,
                embeds.error(
                    f"try again in {error.retry_after:.0f}s.",
                    title="Slow down",
                ),
            )
            return

        log.exception("Unhandled error in %s", ctx.command, exc_info=error)
        await embeds.send(
            ctx, embeds.error("something broke on my end. it has been logged.")
        )

    @commands.hybrid_group(
        name="prefix",
        invoke_without_command=True,
        fallback="show",
        description="Show the command prefix for this server.",
    )
    @commands.guild_only()
    async def prefix(self, ctx):
        current = prefix_for(ctx.guild.id)
        origin = "custom" if is_custom(ctx.guild.id) else "the default"

        embed = embeds.build(
            f"the prefix here is `{current}` ({origin}).",
            title="Command prefix",
        )
        embed.add_field(
            name="Changing it",
            value=(
                f"`{current}prefix set <new>` — set a new prefix\n"
                f"`{current}prefix reset` — go back to `{DEFAULT_PREFIX}`"
            ),
            inline=False,
        )

        footer = "Mentioning me always works as a prefix too."
        if not can_manage(ctx.author):
            footer += " Only the owner or an administrator can change it."
        embed.set_footer(text=footer)

        await embeds.send(ctx, embed)

    @prefix.command(name="set", description="Change the prefix for this server.")
    @app_commands.describe(new="The new prefix, quoted if it ends in a space")
    @owner_or_admin()
    @commands.cooldown(1, 5, commands.BucketType.guild)
    async def prefix_set(self, ctx, *, new: str = None):
        value, problem = validate(new)

        if problem:
            await embeds.send(ctx, embeds.error(problem, title="Invalid prefix"))
            return

        current = prefix_for(ctx.guild.id)
        if value == current:
            await embeds.send(
                ctx, embeds.notice(f"the prefix is already `{value}`.")
            )
            return

        set_prefix(ctx.guild.id, value)

        await embeds.send(
            ctx,
            embeds.notice(
                f"the prefix is now `{value}`. try `{value}help`.",
                title="Prefix updated",
            ),
        )

    @prefix.command(
        name="reset",
        aliases=["default"],
        description="Go back to the default prefix.",
    )
    @owner_or_admin()
    async def prefix_reset(self, ctx):
        removed = clear_prefix(ctx.guild.id)

        if removed is None:
            await embeds.send(
                ctx,
                embeds.notice(
                    f"the prefix is already the default `{DEFAULT_PREFIX}`."
                ),
            )
            return

        await embeds.send(
            ctx,
            embeds.notice(
                f"the prefix is back to `{DEFAULT_PREFIX}`.",
                title="Prefix reset",
            ),
        )


async def setup(bot):
    await bot.add_cog(Prefix(bot))