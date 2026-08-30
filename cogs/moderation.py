import datetime
import logging
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from prefixes import display_prefix
import emojiutils
import templating
from scheduling import tz_for
from storage import Store

log = logging.getLogger(__name__)

_config_store = Store("mod_config.json")
_warn_store = Store("warns.json", default=list)

config = _config_store.load()
warns = _warn_store.load()

TEMPLATE_LIMIT = 3800
REASON_LIMIT = 1000
ENTRY_LIMIT = 120        # longest single blacklist entry
MAX_ENTRIES = 500        # most words the blacklist can hold
NOTICE_DELETE_AFTER = 6  # seconds the "not allowed" ping lingers

# U+3164 hangul filler. Discord renders it as a wide blank.
PAD = "ㅤ"

DEFAULT_TEMPLATE = (
    f":03dc_cake:{PAD}a little mishap has reached our ears!\n"
    f"{PAD}\n"
    f":shortcake1:{PAD}user: {{user}}\n"
    f":strawberri:{PAD}reason: {{reason}}\n"
    f":IceCreamSundae:{PAD}warned by: {{moderator}}\n"
    f":dndexl:{PAD}{{date}}\n"
    f"{PAD}\n"
    "please take note of the rules, dear customer! this keeps things in order."
)

FIELDS = ("user", "reason", "moderator", "count", "date", "time", "when")

ALIASES = {
    "user": "user", "member": "user", "offender": "user", "them": "user",
    "warned": "user", "for": "user",
    "reason": "reason", "why": "reason", "note": "reason", "message": "reason",
    "comment": "reason", "mishap": "reason",
    "moderator": "moderator", "mod": "moderator", "staff": "moderator",
    "warned by": "moderator", "by": "moderator", "author": "moderator",
    "issuer": "moderator",
    "count": "count", "number": "count", "total": "count", "warns": "count",
    "strikes": "count",
    "date": "date", "day": "date",
    "time": "time",
    "when": "when", "posted": "when", "warned at": "when",
}

SAMPLE = {
    "user": "@himeko",
    "reason": "no vouch",
    "moderator": "@staff",
    "count": "3",
    "date": "august 30, 2026",
    "time": "10:14 am",
    "when": "just now",
}


def save_config():
    _config_store.save(config)


def save_warns():
    _warn_store.save(warns)


def get_config(guild_id):
    return config.get(str(guild_id))


def defaults():
    return {
        "channel_id": None,
        "template": DEFAULT_TEMPLATE,
        "ping": False,
        "blacklist": [],
        "exempt_roles": [],
    }


def ensure_config(guild_id):
    key = str(guild_id)
    if key not in config:
        config[key] = defaults()

    settings = config[key]
    for field, value in defaults().items():
        settings.setdefault(field, value)
    return settings


def settings_for(guild_id):
    return get_config(guild_id) or defaults()


def guild_warns(guild_id):
    return [w for w in warns if w["guild_id"] == guild_id]


def count_for(guild_id, user_id):
    return sum(
        1 for w in warns
        if w["guild_id"] == guild_id and w["user_id"] == user_id
    )


def stamp_values(guild, record):
    stamp = int(record.get("created_at") or 0)
    if not stamp:
        return {"date": "", "time": "", "when": ""}

    moment = datetime.datetime.fromtimestamp(stamp, tz_for(guild.id))
    return {
        "date": moment.strftime("%B %d, %Y").lower(),
        "time": moment.strftime("%I:%M %p").lstrip("0").lower(),
        "when": f"<t:{stamp}:R>",
    }


def warn_values(guild, record):
    values = {
        "user": f"<@{record['user_id']}>",
        "reason": record["reason"],
        "moderator": f"<@{record['moderator_id']}>",
        "count": str(record.get("count", 1)),
    }
    values.update(stamp_values(guild, record))
    return values


def render(template, values, guild):
    return templating.render(template, values, ALIASES, guild)


def warn_embed(guild, settings, record):
    body = render(settings["template"], warn_values(guild, record), guild)
    return embeds.build(body[:4096])


def normalize(text):
    """Lowercase and collapse whitespace so matching ignores case and spacing."""
    return " ".join((text or "").lower().split())


def find_blacklisted(content, words):
    """Return the first blacklist entry that appears in the content, else None."""
    haystack = normalize(content)
    if not haystack:
        return None
    for entry in words:
        needle = normalize(entry)
        if needle and needle in haystack:
            return entry
    return None


class TemplateModal(discord.ui.Modal, title="Warn Format"):
    def __init__(self, builder):
        super().__init__()
        self.builder = builder
        self.f_template = discord.ui.TextInput(
            label="Format",
            default=builder.settings["template"][:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        self.add_item(self.f_template)

    async def on_submit(self, interaction):
        text = self.f_template.value.strip()

        if not text:
            await interaction.response.send_message(
                embed=embeds.error("the format cannot be empty."), ephemeral=True
            )
            return

        if len(text) > TEMPLATE_LIMIT:
            await interaction.response.send_message(
                embed=embeds.error(
                    f"keep the format under {TEMPLATE_LIMIT} characters."
                ),
                ephemeral=True,
            )
            return

        await interaction.response.defer()
        self.builder.settings["template"] = text
        save_config()
        await self.builder.refresh()

        notes = []
        missed = templating.unknown(text, ALIASES)
        if missed:
            listed = ", ".join(f"`{{{u}}}`" for u in missed)
            notes.append(
                f"{listed} is not a field i know, so it will print as "
                "written. the fields are "
                + ", ".join(f"`{{{f}}}`" for f in FIELDS)
                + "."
            )

        dead = emojiutils.unresolved_names(text, interaction.guild)
        if dead:
            listed = ", ".join(f"`:{d}:`" for d in dead)
            notes.append(
                f"{listed} does not match an emoji in this server, so it "
                "will print as text. type a backslash before the emoji and "
                "paste what discord gives you instead."
            )

        if notes:
            await interaction.followup.send(
                embed=embeds.error(
                    "saved. two things to check:\n\n" + "\n\n".join(notes)
                    if len(notes) > 1
                    else "saved, but " + notes[0],
                    title="Check the format",
                ),
                ephemeral=True,
            )


class ChannelView(discord.ui.View):
    def __init__(self, builder):
        super().__init__(timeout=300)
        self.builder = builder

    async def interaction_check(self, interaction):
        return interaction.user.id == self.builder.ctx.author.id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Where warns are logged",
        row=0,
    )
    async def pick(self, interaction, select):
        await interaction.response.defer()
        self.builder.settings["channel_id"] = select.values[0].id
        save_config()
        await self.builder.refresh()


class SetupView(discord.ui.View):
    """The deck for the warn log: channel, format and ping."""

    def __init__(self, ctx, settings):
        super().__init__(timeout=900)
        self.ctx = ctx
        self.settings = settings
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                embed=embeds.error("this deck isn't yours.", title="Not yours"),
                ephemeral=True,
            )
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=embeds.error(
                    "you need manage server permission.", title="Not allowed"
                ),
                ephemeral=True,
            )
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    def status_embed(self):
        guild = self.ctx.guild
        settings = self.settings
        channel = guild.get_channel(settings.get("channel_id"))

        lines = [
            f"**Logs drop in** - {channel.mention if channel else 'not set'}",
            f"**Pings the person** - {'yes' if settings['ping'] else 'no'}",
            "",
            f"**Warns recorded** - {len(guild_warns(guild.id))}",
            f"**Blacklisted words** - {len(settings.get('blacklist', []))}",
        ]

        embed = embeds.build("\n".join(lines), title="Warn setup")
        embed.add_field(
            name="Preview",
            value=render(settings["template"], SAMPLE, guild)[:1024],
            inline=False,
        )
        embed.set_footer(text="channel · format · ping — editable below")
        return embed

    async def refresh(self):
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.status_embed(), view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="channel", style=discord.ButtonStyle.secondary, row=0)
    async def channel(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice("pick where warn logs should land."),
            view=ChannelView(self),
            ephemeral=True,
        )

    @discord.ui.button(label="format", style=discord.ButtonStyle.secondary, row=0)
    async def format_button(self, interaction, button):
        await interaction.response.send_modal(TemplateModal(self))

    @discord.ui.button(label="fields", style=discord.ButtonStyle.secondary, row=0)
    async def fields(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(
                "drop any of these into the format and the bot fills them "
                "in:\n\n"
                + "\n".join(f"`{{{f}}}`" for f in FIELDS)
                + "\n\ntype `:name:` for a server emoji and it gets resolved "
                "when the warn is logged.",
                title="Format fields",
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="toggle ping", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_ping(self, interaction, button):
        await interaction.response.defer()
        self.settings["ping"] = not self.settings["ping"]
        save_config()
        await self.refresh()

    @discord.ui.button(label="reset format", style=discord.ButtonStyle.danger, row=1)
    async def reset_format(self, interaction, button):
        await interaction.response.defer()
        self.settings["template"] = DEFAULT_TEMPLATE
        save_config()
        await self.refresh()


class Moderation(commands.Cog):
    """Manual warnings with logs, plus a word blacklist that deletes and flags."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(
                ctx, embeds.error("this command only works in a server.")
            )
        elif isinstance(error, commands.MissingPermissions):
            await embeds.send(
                ctx,
                embeds.error(
                    "you do not have permission to use that.", title="Not allowed"
                ),
            )
        elif isinstance(error, commands.MemberNotFound):
            await embeds.send(ctx, embeds.error("i could not find that member."))
        elif isinstance(error, commands.RoleNotFound):
            await embeds.send(ctx, embeds.error("i could not find that role."))
        elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await embeds.send(
                ctx, embeds.error("check the arguments and try again.", title="Bad arguments")
            )
        else:
            log.exception("Unhandled error in %s", ctx.command, exc_info=error)
            await embeds.send(
                ctx, embeds.error("something broke on my end. it has been logged.")
            )

    # ------------------------------------------------------------------ filter

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild or not message.content:
            return

        author = message.author
        if author.bot or not isinstance(author, discord.Member):
            return

        settings = get_config(message.guild.id)
        if not settings:
            return

        words = settings.get("blacklist") or []
        if not words:
            return

        # Staff and bots are never filtered.
        if author.guild_permissions.manage_messages:
            return

        exempt = settings.get("exempt_roles") or []
        if exempt and any(role.id in exempt for role in author.roles):
            return

        matched = find_blacklisted(message.content, words)
        if not matched:
            return

        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound, discord.HTTPException):
            # No Manage Messages, or it is already gone. Nothing to flag.
            return

        try:
            await message.channel.send(
                f"🚨 {author.mention}, the word **{matched}** isn't allowed. "
                "kindly censor or adjust the spelling for server safety.",
                delete_after=NOTICE_DELETE_AFTER,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[author]
                ),
            )
        except discord.HTTPException:
            pass

    # ------------------------------------------------------------------- warns

    @commands.hybrid_group(
        name="warn",
        invoke_without_command=True,
        fallback="add",
        description="Warn a member and log it.",
    )
    @app_commands.describe(user="Who to warn", reason="Why you are warning them")
    @app_commands.default_permissions(manage_messages=True)
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx, user: discord.Member, *, reason: str):
        await ctx.defer(ephemeral=True)

        settings = settings_for(ctx.guild.id)

        channel = ctx.guild.get_channel(settings.get("channel_id"))
        if channel is None:
            await embeds.send(
                ctx,
                embeds.error(
                    "no warn-log channel set yet. run "
                    f"`{display_prefix(ctx)}warn setup`.",
                    title="Not set up",
                ),
            )
            return

        if user.bot:
            await embeds.send(ctx, embeds.error("you cannot warn a bot."))
            return

        text = reason.strip()
        if not text:
            await embeds.send(ctx, embeds.error("give a reason for the warn."))
            return
        if len(text) > REASON_LIMIT:
            await embeds.send(
                ctx,
                embeds.error(f"keep the reason under {REASON_LIMIT} characters."),
            )
            return

        if not channel.permissions_for(ctx.guild.me).send_messages:
            await embeds.send(
                ctx, embeds.error("i cannot post in the warn-log channel.")
            )
            return

        record = {
            "id": uuid.uuid4().hex[:8],
            "guild_id": ctx.guild.id,
            "user_id": user.id,
            "moderator_id": ctx.author.id,
            "reason": text,
            "created_at": int(time.time()),
            "count": count_for(ctx.guild.id, user.id) + 1,
            "message_id": None,
        }

        embed = warn_embed(ctx.guild, settings, record)
        ping = settings.get("ping", False)

        try:
            sent = await channel.send(
                content=user.mention if ping else None,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=ping
                ),
            )
        except discord.Forbidden:
            await embeds.send(
                ctx, embeds.error("i cannot post in the warn-log channel.")
            )
            return
        except discord.HTTPException:
            log.exception("warn log rejected in %s", channel.id)
            await embeds.send(
                ctx, embeds.error("discord turned that warn down. check the log.")
            )
            return

        record["message_id"] = sent.id
        warns.append(record)
        save_warns()

        await embeds.send(
            ctx,
            embeds.notice(
                f"warned {user.display_name}. that is warn "
                f"#{record['count']} for them. {sent.jump_url}",
                title="Warned",
            ),
            ephemeral=True,
        )

    @warn.command(
        name="setup",
        description="Customise where warns log and how they look.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def warn_setup(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()

        view = SetupView(ctx, settings)
        view.message = await ctx.send(
            embed=view.status_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @warn.command(name="list", description="Show a member's warns.")
    @app_commands.describe(user="Whose warns to show")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def warn_list(self, ctx, user: discord.Member):
        records = sorted(
            (w for w in guild_warns(ctx.guild.id) if w["user_id"] == user.id),
            key=lambda w: w["created_at"],
        )

        if not records:
            await embeds.send(
                ctx, embeds.notice(f"{user.display_name} has no warns.")
            )
            return

        lines = []
        for w in records:
            when = f"<t:{int(w['created_at'])}:d>" if w.get("created_at") else ""
            lines.append(f"`{w['id']}` · {when} · {w['reason'][:120]}")

        await embeds.send(
            ctx,
            embeds.build(
                "\n".join(lines)[:4000],
                title=f"{user.display_name}'s warns ({len(records)})",
            ),
        )

    @warn.command(name="remove", aliases=["delete", "del"], description="Remove a single warn by id.")
    @app_commands.describe(warn_id="The id shown in warn list")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def warn_remove(self, ctx, warn_id: str):
        wanted = warn_id.strip().lower()
        for index, w in enumerate(warns):
            if w["guild_id"] == ctx.guild.id and w["id"] == wanted:
                warns.pop(index)
                save_warns()
                await embeds.send(
                    ctx,
                    embeds.notice(f"removed warn `{wanted}`.", title="Warn removed"),
                )
                return
        await embeds.send(ctx, embeds.error(f"no warn here with the id `{wanted}`."))

    @warn.command(name="clear", description="Clear every warn on a member.")
    @app_commands.describe(user="Whose warns to wipe")
    @commands.guild_only()
    @commands.has_permissions(manage_messages=True)
    async def warn_clear(self, ctx, user: discord.Member):
        before = len(warns)
        warns[:] = [
            w for w in warns
            if not (w["guild_id"] == ctx.guild.id and w["user_id"] == user.id)
        ]
        removed = before - len(warns)

        if not removed:
            await embeds.send(
                ctx, embeds.notice(f"{user.display_name} has no warns to clear.")
            )
            return

        save_warns()
        await embeds.send(
            ctx,
            embeds.notice(
                f"cleared {removed} warn{'' if removed == 1 else 's'} "
                f"from {user.display_name}.",
                title="Warns cleared",
            ),
        )

    # --------------------------------------------------------------- blacklist

    @commands.hybrid_group(
        name="blacklist",
        aliases=["bl"],
        invoke_without_command=True,
        fallback="show",
        description="Manage the word blacklist.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def blacklist(self, ctx):
        await self.show_blacklist(ctx)

    async def show_blacklist(self, ctx):
        settings = get_config(ctx.guild.id)
        words = (settings or {}).get("blacklist", []) if settings else []

        if not words:
            await embeds.send(
                ctx,
                embeds.notice(
                    "nothing is blacklisted yet. add one with "
                    f"`{display_prefix(ctx)}blacklist add <word>`.",
                    title="Blacklist",
                ),
            )
            return

        listed = "\n".join(f"`{w}`" for w in words)
        await embeds.send(
            ctx, embeds.build(listed[:4000], title=f"Blacklist ({len(words)})")
        )

    @blacklist.command(name="add", description="Add a word or phrase to the blacklist.")
    @app_commands.describe(word="The word or phrase to block")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def blacklist_add(self, ctx, *, word: str):
        entry = " ".join(word.split())
        if not entry:
            await embeds.send(ctx, embeds.error("give a word or phrase to blacklist."))
            return
        if len(entry) > ENTRY_LIMIT:
            await embeds.send(
                ctx, embeds.error(f"keep entries under {ENTRY_LIMIT} characters.")
            )
            return

        settings = ensure_config(ctx.guild.id)
        existing = settings["blacklist"]

        if any(normalize(e) == normalize(entry) for e in existing):
            await embeds.send(
                ctx, embeds.notice(f"`{entry}` is already blacklisted.")
            )
            return
        if len(existing) >= MAX_ENTRIES:
            await embeds.send(
                ctx, embeds.error(f"the blacklist is full ({MAX_ENTRIES} entries).")
            )
            return

        existing.append(entry)
        save_config()
        await embeds.send(
            ctx,
            embeds.notice(f"added `{entry}` to the blacklist.", title="Blacklisted"),
        )

    @blacklist.command(
        name="remove",
        aliases=["delete", "del"],
        description="Remove a word or phrase from the blacklist.",
    )
    @app_commands.describe(word="The word or phrase to unblock")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def blacklist_remove(self, ctx, *, word: str):
        entry = " ".join(word.split())
        settings = ensure_config(ctx.guild.id)
        existing = settings["blacklist"]

        for current in list(existing):
            if normalize(current) == normalize(entry):
                existing.remove(current)
                save_config()
                await embeds.send(
                    ctx,
                    embeds.notice(f"removed `{current}` from the blacklist.", title="Removed"),
                )
                return

        await embeds.send(ctx, embeds.error(f"`{entry}` is not on the blacklist."))

    @blacklist.command(name="clear", description="Remove every blacklisted word.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def blacklist_clear(self, ctx):
        settings = ensure_config(ctx.guild.id)
        if not settings["blacklist"]:
            await embeds.send(ctx, embeds.notice("the blacklist is already empty."))
            return
        settings["blacklist"] = []
        save_config()
        await embeds.send(
            ctx, embeds.notice("cleared the blacklist.", title="Blacklist cleared")
        )

    @blacklist.command(
        name="exempt",
        description="Toggle a role that bypasses the filter, or list them.",
    )
    @app_commands.describe(role="Role to toggle. Leave empty to list bypass roles.")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def blacklist_exempt(self, ctx, role: discord.Role = None):
        settings = ensure_config(ctx.guild.id)
        exempt = settings["exempt_roles"]

        if role is None:
            if not exempt:
                await embeds.send(
                    ctx,
                    embeds.notice(
                        "no roles bypass the filter yet. staff (manage messages) "
                        "and bots always skip it.",
                        title="Filter bypass roles",
                    ),
                )
                return
            listed = ", ".join(f"<@&{rid}>" for rid in exempt)
            await embeds.send(
                ctx,
                embeds.build(
                    "these roles bypass the word filter:\n\n" + listed,
                    title="Filter bypass roles",
                ),
            )
            return

        if role.id in exempt:
            exempt.remove(role.id)
            save_config()
            await embeds.send(
                ctx,
                embeds.notice(f"{role.mention} is filtered again.", title="Updated"),
            )
        else:
            exempt.append(role.id)
            save_config()
            await embeds.send(
                ctx,
                embeds.notice(
                    f"{role.mention} now bypasses the word filter.", title="Updated"
                ),
            )


async def setup(bot):
    await bot.add_cog(Moderation(bot))