import logging

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from prefixes import prefix_for

log = logging.getLogger(__name__)

HOME = "__home__"
SELECT_LIMIT = 25
BODY_LIMIT = 3800
BLURB_LIMIT = 90
TIMEOUT = 180

ELBOW = "╰"


def lower_first(text):
    """Blurbs read as lowercase fragments, but leave proper nouns alone."""
    return text[:1].lower() + text[1:] if text else text


def total(cmds):
    """A group and its subcommands are all commands the person can run."""
    return sum(1 + len(subcommands_of(c)) for c in cmds)


def counted(cmds):
    n = total(cmds)
    return f"`{n} command{'' if n == 1 else 's'}`"


def describe(command):
    """A one line summary, pulled from wherever the command happens to keep it."""
    return (
        command.short_doc
        or command.description
        or "no description yet."
    )


def blurb(cog):
    """A cog's summary comes from its class docstring."""
    return (cog.description or "").strip().split("\n")[0]


def usage(command, prefix):
    """Built from the parameters directly.

    discord.py's own `signature` wraps attachment parameters in <> even when
    they have a default, which advertises them as required when they aren't.
    """
    parts = []

    for name, param in command.clean_params.items():
        label = name
        if param.kind is param.VAR_POSITIONAL:
            label = f"{name}..."
        shown = getattr(param, "displayed_default", None)
        if shown and shown not in ("True", "False", "None"):
            label = f"{name}={shown}"
        parts.append(f"<{label}>" if param.required else f"[{label}]")

    joined = " ".join(parts)
    return f"{prefix}{command.qualified_name} {joined}".strip()


def subcommands_of(command):
    if not isinstance(command, commands.Group):
        return []
    return sorted(
        (c for c in command.commands if not c.hidden), key=lambda c: c.name
    )


def visible(command):
    """Hidden is inherited. A hidden group hides everything under it."""
    return not command.hidden and not any(p.hidden for p in command.parents)


async def runnable(command, ctx):
    if not visible(command):
        return False
    try:
        return await command.can_run(ctx)
    except commands.CommandError:
        return False


async def collect(bot, ctx):
    """Map every cog to the commands this invoker is actually allowed to run."""
    found = {}

    for name, cog in bot.cogs.items():
        if name == "Help":
            continue

        allowed = [c for c in cog.get_commands() if await runnable(c, ctx)]
        if allowed:
            found[name] = sorted(allowed, key=lambda c: c.name)

    return dict(sorted(found.items()))


def lines_for(command, prefix):
    out = [f"`{usage(command, prefix)}` — {lower_first(describe(command))}"]
    for sub in subcommands_of(command):
        out.append(
            f"{ELBOW} `{usage(sub, prefix)}` — {lower_first(describe(sub))}"
        )
    return out


def overview_embed(bot, ctx, categories, prefix):
    grand = sum(total(c) for c in categories.values())

    if not categories:
        return embeds.notice(
            "there is nothing here you can run.", title="Command help"
        )

    intro = (
        f"prefix is `{prefix}`. pick a category below, or use "
        f"`{prefix}help <command>` for one command in detail."
    )

    blocks, hidden = [], 0
    for name, cmds in categories.items():
        cog = bot.get_cog(name)
        summary = lower_first(blurb(cog)) if cog else ""

        block = f"**{name}**"
        if summary:
            block += f"\n{ELBOW} {summary}"
        block += f"\n\n{counted(cmds)}"

        if len(intro) + sum(len(b) + 2 for b in blocks + [block]) > BODY_LIMIT:
            hidden += 1
            continue
        blocks.append(block)

    body = intro + "\n\n" + "\n\n".join(blocks)
    if hidden:
        body += f"\n\n{ELBOW} and {hidden} more category(s)."

    embed = embeds.build(body, title="Command help")

    if bot.user and bot.user.display_avatar:
        embed.set_thumbnail(url=bot.user.display_avatar.url)

    embed.set_footer(
        text=f"{grand} command{'' if grand == 1 else 's'} · "
        f"requested by {ctx.author.display_name}"
    )
    return embed


def category_embed(bot, name, cmds, prefix):
    cog = bot.get_cog(name)

    body, hidden = [], 0
    for command in cmds:
        chunk = lines_for(command, prefix)
        if sum(len(line) + 1 for line in body + chunk) > BODY_LIMIT:
            hidden += 1
            continue
        body.extend(chunk)

    if hidden:
        body.append(f"\n{ELBOW} {hidden} more. use `{prefix}help <command>`.")

    embed = embeds.build("\n".join(body), title=name)

    summary = blurb(cog) if cog else ""
    if summary:
        embed.set_author(name=lower_first(summary))

    n = total(cmds)
    embed.set_footer(text=f"{n} command{'' if n == 1 else 's'}")
    return embed


def command_embed(command, prefix):
    embed = embeds.build(
        lower_first(describe(command)),
        title=f"{prefix}{command.qualified_name}",
    )
    embed.add_field(name="Usage", value=f"`{usage(command, prefix)}`", inline=False)

    if command.aliases:
        embed.add_field(
            name="Aliases",
            value=" ".join(f"`{prefix}{a}`" for a in command.aliases),
            inline=False,
        )

    subs = subcommands_of(command)
    if subs:
        embed.add_field(
            name="Subcommands",
            value="\n".join(
                f"`{usage(s, prefix)}` — {lower_first(describe(s))}"
                for s in subs
            ),
            inline=False,
        )

    if command.cog:
        embed.set_footer(text=command.cog.qualified_name)
    return embed


class CategorySelect(discord.ui.Select):
    def __init__(self, bot, categories, prefix):
        self.bot = bot
        self.categories = categories
        self.prefix = prefix

        options = [
            discord.SelectOption(
                label="Overview", value=HOME, description="Back to the category list"
            )
        ]

        for name in list(categories)[: SELECT_LIMIT - 1]:
            cog = bot.get_cog(name)
            summary = blurb(cog) if cog else ""
            options.append(
                discord.SelectOption(
                    label=name,
                    value=name,
                    description=summary[:BLURB_LIMIT] or None,
                )
            )

        super().__init__(placeholder="Browse a category", options=options)

    async def callback(self, interaction):
        choice = self.values[0]

        if choice == HOME:
            embed = overview_embed(
                self.bot, self.view.ctx, self.categories, self.prefix
            )
        else:
            embed = category_embed(
                self.bot, choice, self.categories[choice], self.prefix
            )

        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, bot, ctx, categories, prefix):
        super().__init__(timeout=TIMEOUT)
        self.ctx = ctx
        self.prefix = prefix
        self.message = None
        self.add_item(CategorySelect(bot, categories, prefix))

    async def interaction_check(self, interaction):
        if interaction.user.id == self.ctx.author.id:
            return True

        await interaction.response.send_message(
            embed=embeds.error(
                f"this menu belongs to someone else. run `{self.prefix}help` "
                "to get your own.",
                title="Not your menu",
            ),
            ephemeral=True,
        )
        return False

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Help(commands.Cog):
    """Browse everything the bot can do."""

    def __init__(self, bot):
        self.bot = bot

    def lookup(self, query, prefix):
        wanted = query.strip()
        if wanted.startswith(prefix):
            wanted = wanted[len(prefix):].strip()
        return self.bot.get_command(wanted)

    @commands.hybrid_command(
        name="help",
        aliases=["h", "commands"],
        description="Show what the bot can do.",
    )
    @app_commands.describe(query="A command or category name")
    async def help_command(self, ctx, *, query: str = None):
        prefix = prefix_for(ctx.guild.id if ctx.guild else None)
        categories = await collect(self.bot, ctx)

        if query:
            match = self.lookup(query, prefix)
            if match and not match.hidden:
                await embeds.send(ctx, command_embed(match, prefix))
                return

            for name in categories:
                if name.lower() == query.strip().lower():
                    await embeds.send(
                        ctx,
                        category_embed(self.bot, name, categories[name], prefix),
                    )
                    return

            await embeds.send(
                ctx,
                embeds.error(
                    f"nothing called `{query}`. try `{prefix}help` on its own.",
                    title="Not found",
                ),
            )
            return

        view = HelpView(self.bot, ctx, categories, prefix) if categories else None
        message = await ctx.send(
            embed=overview_embed(self.bot, ctx, categories, prefix),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        if view:
            view.message = message

    @help_command.autocomplete("query")
    async def query_autocomplete(self, interaction, current):
        current = current.lower().lstrip(".")
        seen = set()

        for command in self.bot.walk_commands():
            if not visible(command) or command.qualified_name == "help":
                continue
            if current in command.qualified_name.lower():
                seen.add(command.qualified_name)

        for name, cog in self.bot.cogs.items():
            if name == "Help" or current not in name.lower():
                continue
            if any(visible(c) for c in cog.get_commands()):
                seen.add(name)

        return [
            app_commands.Choice(name=n, value=n) for n in sorted(seen)[:25]
        ]


async def setup(bot):
    bot.remove_command("help")
    await bot.add_cog(Help(bot))