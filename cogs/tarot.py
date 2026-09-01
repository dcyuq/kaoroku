import datetime
import logging
import time
import uuid

import discord
from discord import app_commands
from discord.ext import commands

import attach
import embeds
from prefixes import display_prefix
import emojiutils
import templating
from scheduling import tz_for
from storage import Store

log = logging.getLogger(__name__)

_config_store = Store("tarot_config.json")
_reading_store = Store("tarot.json", default=list)

config = _config_store.load()
readings = _reading_store.load()

TEMPLATE_LIMIT = 3800
READING_LIMIT = 3000
COOLDOWN_SECONDS = 15

# U+3164 hangul filler. Discord renders it as a wide blank.
PAD = "\u3164"

DEFAULT_TEMPLATE = (
    f":03dc_cake:{PAD}your reading is ready, {{user}}\n"
    f"{PAD}\n"
    f":shortcake1:{PAD}{{tarot}}\n"
    f"{PAD}\n"
    f":strawberri:{PAD}read for you by {{reader}}\n"
    f":IceCreamSundae:{PAD}loved it? leave a vouch in {{vouch channel}}\n"
    f":dndexl:{PAD}{{date}}"
)

FIELDS = ("user", "tarot", "reader", "vouch channel", "channel",
          "date", "time", "when", "count")

ALIASES = {
    "user": "user", "querent": "user", "client": "user", "recipient": "user",
    "for": "user", "them": "user",
    "tarot": "tarot", "reading": "tarot", "cards": "tarot", "spread": "tarot",
    "result": "tarot", "message": "tarot", "text": "tarot",
    "reader": "reader", "read by": "reader", "by": "reader", "staff": "reader",
    "vouch channel": "vouch_channel", "vouches": "vouch_channel",
    "vouch": "vouch_channel", "vouch here": "vouch_channel",
    "channel": "channel", "tarot channel": "channel", "here": "channel",
    "date": "date", "day": "date",
    "time": "time",
    "when": "when", "posted": "when",
    "count": "count", "number": "count", "reading number": "count",
}

SAMPLE = {
    "user": "@querent",
    "tarot": "the star, reversed. you have been pouring into others and "
             "leaving little for yourself. rest is not a detour.",
    "reader": "@reader",
    "vouch_channel": "#vouches",
    "channel": "#readings",
    "date": "august 22, 2026",
    "time": "10:22 pm",
    "when": "just now",
    "count": "3",
}


def save_config():
    _config_store.save(config)


def save_readings():
    _reading_store.save(readings)


def get_config(guild_id):
    return config.get(str(guild_id))


def defaults():
    return {
        "vouch_channel_id": None,
        "log_channel_id": None,
        "template": DEFAULT_TEMPLATE,
        "ping": True,
    }


def ensure_config(guild_id):
    key = str(guild_id)
    if key not in config:
        config[key] = defaults()

    settings = config[key]
    for field, value in defaults().items():
        settings.setdefault(field, value)

    # Left over from when the destination was a server-wide setting.
    for stale in ("channel_id", "delivery", "staff_only"):
        settings.pop(stale, None)

    return settings


def settings_for(guild_id):
    return get_config(guild_id) or defaults()


def count_for(guild_id, user_id):
    return sum(
        1 for r in readings
        if r["guild_id"] == guild_id and r["user_id"] == user_id
    )


def channel_text(guild, channel_id, fallback):
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if channel else fallback


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


def reading_values(guild, settings, record):
    values = {
        "user": f"<@{record['user_id']}>",
        "tarot": record["tarot"],
        "reader": f"<@{record['reader_id']}>",
        "vouch_channel": channel_text(
            guild, settings.get("vouch_channel_id"), "the vouch channel"
        ),
        "channel": channel_text(
            guild, record.get("channel_id"), "your dms"
        ),
        "count": str(record.get("count", 1)),
    }
    values.update(stamp_values(guild, record))
    return values


def render(template, values, guild):
    return templating.render(template, values, ALIASES, guild)


def reading_embed(guild, settings, record):
    body = render(settings["template"], reading_values(guild, settings, record), guild)
    return embeds.build(body[:4096])


def reading_log_embed(guild, record, destination_text, jump_url, file_count):
    """A staff-facing record of exactly what a reader sent out."""
    lines = [
        f"**reader** - <@{record['reader_id']}>",
        f"**for** - <@{record['user_id']}>",
        f"**sent to** - {destination_text}",
        f"**reading #** - {record.get('count', 1)}",
    ]
    if file_count:
        lines.append(f"**files** - {file_count}")
    if jump_url:
        lines.append(f"**message** - [jump]({jump_url})")

    embed = embeds.build("\n".join(lines), title="Reading sent")
    embed.add_field(
        name="What was sent",
        value=(record.get("tarot") or "-")[:1024],
        inline=False,
    )
    embed.timestamp = discord.utils.utcnow()
    return embed


async def send_log(guild, embed, files=None):
    """Drop a record in the reading log channel, if one is set."""
    settings = get_config(guild.id)
    if not settings:
        return
    channel = guild.get_channel(settings.get("log_channel_id"))
    if channel is None:
        return
    if not channel.permissions_for(guild.me).send_messages:
        return

    kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
    if files:
        kwargs["files"] = files

    try:
        await channel.send(**kwargs)
    except (discord.Forbidden, discord.HTTPException):
        log.exception("tarot log post failed in %s", channel.id)


class TemplateModal(discord.ui.Modal, title="Reading Format"):
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
    """Used for both the reading channel and the vouch channel."""

    def __init__(self, builder, key):
        super().__init__(timeout=300)
        self.builder = builder
        self.key = key

    async def interaction_check(self, interaction):
        return interaction.user.id == self.builder.ctx.author.id

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news],
        placeholder="Pick a channel",
        row=0,
    )
    async def pick(self, interaction, select):
        await interaction.response.defer()
        self.builder.settings[self.key] = select.values[0].id
        save_config()
        await self.builder.refresh()


class SetupView(discord.ui.View):
    """The deck. Everything about readings is reachable from here."""

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

        vouch = guild.get_channel(settings.get("vouch_channel_id"))
        log_channel = guild.get_channel(settings.get("log_channel_id"))

        lines = [
            f"**Vouch channel** - {vouch.mention if vouch else 'not set'}",
            f"**Reading log** - {log_channel.mention if log_channel else 'not set'}",
            f"**Pings the person** - {'yes' if settings['ping'] else 'no'}",
            "",
            "readings go to whichever channel the reader picks when they run "
            "the command, or straight to dms if they pick none.",
            "",
            "when a reading log is set, every reading is copied there so staff "
            "can see exactly what was sent.",
            "",
            f"**Readings given** - "
            f"{len([r for r in readings if r['guild_id'] == guild.id])}",
        ]

        if vouch is None and "{vouch channel}" in settings["template"]:
            lines.append("")
            lines.append(
                "your format mentions the vouch channel but none is set, so "
                "it prints as plain text."
            )

        embed = embeds.build("\n".join(lines), title="Tarot setup")
        embed.add_field(
            name="Preview",
            value=render(settings["template"], SAMPLE, guild)[:1024],
            inline=False,
        )
        embed.set_footer(
            text="vouch channel · reading log · format · ping — all editable below"
        )
        return embed

    async def refresh(self):
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.status_embed(), view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="vouch channel", style=discord.ButtonStyle.secondary, row=0)
    async def vouch_channel(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(
                "pick the channel `{vouch channel}` should point at."
            ),
            view=ChannelView(self, "vouch_channel_id"),
            ephemeral=True,
        )

    @discord.ui.button(label="log channel", style=discord.ButtonStyle.secondary, row=0)
    async def log_channel(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(
                "pick the channel where a copy of every reading is logged. "
                "leave it unset to keep no log."
            ),
            view=ChannelView(self, "log_channel_id"),
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
                + "\n\n`{vouch channel}` becomes a clickable channel once you "
                "set one above. type `:name:` for a server emoji. attached "
                "files are added under the text automatically, the first "
                "image inside the embed and the rest below it.",
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


DELIVERY_CHOICES = [
    ("dm", "Direct message only", "Nobody else sees it."),
    ("channel", "A channel", "Pick which one below."),
    ("both", "Both", "DM them and post it in a channel."),
]


class DeliverySelect(discord.ui.Select):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="Where should this reading go?",
            row=0,
            options=[
                discord.SelectOption(
                    label=label,
                    value=key,
                    description=blurb,
                    default=(key == panel.mode),
                )
                for key, label, blurb in DELIVERY_CHOICES
            ],
        )

    async def callback(self, interaction):
        self.panel.mode = self.values[0]
        await self.panel.redraw(interaction)


class DestinationSelect(discord.ui.ChannelSelect):
    def __init__(self, panel):
        self.panel = panel
        super().__init__(
            placeholder="Which channel?",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            row=1,
            disabled=panel.mode == "dm",
        )

    async def callback(self, interaction):
        self.panel.channel_id = self.values[0].id
        await self.panel.redraw(interaction)


class DeliveryPanel(discord.ui.View):
    """Asks where the reading goes, then sends it."""

    def __init__(self, ctx, settings, record, pictures):
        super().__init__(timeout=300)
        self.ctx = ctx
        self.settings = settings
        self.record = record
        self.pictures = pictures or []
        # only an image can sit inside the embed; the rest ride along as
        # ordinary attachments under it.
        self.first_image = next(
            (p for p in self.pictures if p.is_image), None
        )
        self.mode = "dm"
        self.channel_id = None
        self.message = None
        self.rebuild()

    def rebuild(self):
        self.clear_items()
        self.add_item(DeliverySelect(self))
        self.add_item(DestinationSelect(self))
        self.add_item(self.send_button)
        self.add_item(self.cancel_button)

    @property
    def channel(self):
        if self.channel_id is None:
            return None
        return self.ctx.guild.get_channel(self.channel_id)

    @property
    def needs_channel(self):
        return self.mode in ("channel", "both")

    async def interaction_check(self, interaction):
        if interaction.user.id == self.ctx.author.id:
            return True
        await interaction.response.send_message(
            embed=embeds.error("this reading isn't yours.", title="Not yours"),
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

    def preview(self):
        record = dict(self.record)
        record["channel_id"] = self.channel_id
        embed = reading_embed(self.ctx.guild, self.settings, record)
        if self.first_image is not None:
            embed.set_image(url=self.first_image.reference)

        if self.needs_channel and self.channel is None:
            note = "pick a channel below before sending."
        elif self.mode == "dm":
            note = f"going to {self.record['display']}'s dms only."
        elif self.mode == "channel":
            note = f"going to {self.channel.mention}."
        else:
            note = (
                f"going to {self.channel.mention} and "
                f"{self.record['display']}'s dms."
            )
        count = len(self.pictures)
        if count:
            note += f" {count} file{'' if count == 1 else 's'} attached."
        embed.set_footer(text=note)
        return embed

    async def redraw(self, interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=self.preview(), view=self)

    @discord.ui.button(label="send", style=discord.ButtonStyle.success, row=2)
    async def send_button(self, interaction, button):
        if self.needs_channel and self.channel is None:
            await interaction.response.send_message(
                embed=embeds.error("pick a channel first."), ephemeral=True
            )
            return

        channel = self.channel if self.needs_channel else None

        if channel is not None:
            if not channel.permissions_for(interaction.user).send_messages:
                await interaction.response.send_message(
                    embed=embeds.error("you cannot post in that channel."),
                    ephemeral=True,
                )
                return
            if not channel.permissions_for(interaction.guild.me).send_messages:
                await interaction.response.send_message(
                    embed=embeds.error("i cannot post in that channel."),
                    ephemeral=True,
                )
                return

        await interaction.response.defer()

        user = interaction.guild.get_member(self.record["user_id"])
        record = dict(self.record)
        record["channel_id"] = channel.id if channel else None
        record.pop("display", None)

        embed = reading_embed(interaction.guild, self.settings, record)
        _, image_ref = attach.attachment_payload(self.pictures, self.first_image)
        if image_ref:
            embed.set_image(url=image_ref)

        ping = self.settings.get("ping", True)
        sent_dm = False
        posted = None
        dm_error = None

        if self.mode in ("dm", "both") and user is not None:
            files, _ = attach.attachment_payload(self.pictures, self.first_image)
            try:
                await user.send(embed=embed, files=files or None)
                sent_dm = True
            except discord.Forbidden:
                dm_error = "their dms are closed"
            except discord.HTTPException:
                log.exception("tarot dm failed for %s", record["user_id"])
                dm_error = "discord turned the dm down"

        if channel is not None:
            files, _ = attach.attachment_payload(self.pictures, self.first_image)
            try:
                posted = await channel.send(
                    content=user.mention if (ping and user) else None,
                    embed=embed,
                    files=files or None,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=ping
                    ),
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    embed=embeds.error("i cannot post in that channel."),
                    ephemeral=True,
                )
                return
            except discord.HTTPException:
                log.exception("tarot post rejected in %s", channel.id)
                await interaction.followup.send(
                    embed=embeds.error(
                        "discord turned that reading down. check the log."
                    ),
                    ephemeral=True,
                )
                return

        if not sent_dm and posted is None:
            await interaction.followup.send(
                embed=embeds.error(
                    f"the reading did not go anywhere, {dm_error}. send it to "
                    "a channel instead.",
                    title="Not delivered",
                ),
                ephemeral=True,
            )
            return

        record["message_id"] = posted.id if posted else None
        readings.append(record)
        save_readings()

        name = user.display_name if user else "them"
        parts = []
        if sent_dm:
            parts.append(f"sent to {name}'s dms")
        if posted is not None:
            parts.append(f"posted in {channel.mention}")

        body = " and ".join(parts) + "."
        if self.mode in ("dm", "both") and not sent_dm:
            body += f" the dm failed though, {dm_error}."
        body += f" that is reading #{record['count']} for {name}."
        if posted is not None:
            body += f" {posted.jump_url}"

        if self.settings.get("log_channel_id"):
            dest_bits = []
            if sent_dm:
                dest_bits.append(f"{name}'s dms")
            if posted is not None:
                dest_bits.append(channel.mention)
            log_files, log_ref = attach.attachment_payload(
                self.pictures, self.first_image
            )
            log_embed = reading_log_embed(
                interaction.guild,
                record,
                " and ".join(dest_bits) or "nowhere",
                posted.jump_url if posted else None,
                len(self.pictures),
            )
            if log_ref:
                log_embed.set_image(url=log_ref)
            await send_log(interaction.guild, log_embed, files=log_files)

        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                await self.message.edit(
                    embed=embeds.notice(body, title="Reading delivered"),
                    view=self,
                )
            except discord.HTTPException:
                pass
        self.stop()

    @discord.ui.button(label="cancel", style=discord.ButtonStyle.secondary, row=2)
    async def cancel_button(self, interaction, button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=embeds.notice("dropped this one. nothing was sent."),
            view=self,
        )
        self.stop()


class Tarot(commands.Cog):
    """Send tarot readings by dm, in a channel, or both."""

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
                    "you need manage server permission for that.",
                    title="Not allowed",
                ),
            )
            return
        if isinstance(error, commands.CommandOnCooldown):
            await embeds.send(
                ctx,
                embeds.error(
                    f"try again in {error.retry_after:.0f}s.", title="Slow down"
                ),
            )
            return

        log.exception("Unhandled error in %s", ctx.command, exc_info=error)
        await embeds.send(
            ctx, embeds.error("something broke on my end. it has been logged.")
        )

    @commands.hybrid_group(
        name="tarot",
        invoke_without_command=True,
        fallback="send",
        description="Send someone a tarot reading.",
    )
    @app_commands.describe(
        user="Who the reading is for",
        tarot="The reading itself",
        image="An optional card photo, spread or file",
        image2="Another file (optional)",
        image3="Another file (optional)",
        image4="Another file (optional)",
        image5="Another file (optional)",
    )
    @commands.guild_only()
    @commands.cooldown(1, COOLDOWN_SECONDS, commands.BucketType.user)
    async def tarot(
        self,
        ctx,
        user: discord.Member,
        image: discord.Attachment = None,
        image2: discord.Attachment = None,
        image3: discord.Attachment = None,
        image4: discord.Attachment = None,
        image5: discord.Attachment = None,
        *,
        tarot: str,
    ):
        # Reading uploads and sending two messages takes longer than the
        # three seconds Discord allows before the interaction dies.
        await ctx.defer(ephemeral=True)

        settings = settings_for(ctx.guild.id)

        if user.bot:
            await embeds.send(ctx, embeds.error("you cannot read for a bot."))
            return

        text = tarot.strip()
        if not text:
            await embeds.send(ctx, embeds.error("the reading is empty."))
            return
        if len(text) > READING_LIMIT:
            await embeds.send(
                ctx,
                embeds.error(
                    f"keep the reading under {READING_LIMIT} characters."
                ),
            )
            return

        # The five slots cover slash uploads; a prefix message can carry up
        # to ten files, so pull any extras straight off the message too.
        chosen = [image, image2, image3, image4, image5]
        chosen += list(getattr(ctx.message, "attachments", None) or [])
        seen, attachments = set(), []
        for item in chosen:
            if item is None or item.id in seen:
                continue
            seen.add(item.id)
            attachments.append(item)

        if len(attachments) > attach.MAX_FILES:
            await embeds.send(
                ctx,
                embeds.error(
                    f"that is a lot of files. i can take up to "
                    f"{attach.MAX_FILES} at once."
                ),
            )
            return

        pictures, problem = await attach.read_files(attachments)
        if problem:
            await embeds.send(ctx, embeds.error(problem))
            return

        record = {
            "id": uuid.uuid4().hex[:8],
            "guild_id": ctx.guild.id,
            "user_id": user.id,
            "reader_id": ctx.author.id,
            "tarot": text,
            "created_at": int(time.time()),
            "count": count_for(ctx.guild.id, user.id) + 1,
            "display": user.display_name,
        }

        panel = DeliveryPanel(ctx, settings, record, pictures)
        panel.message = await ctx.send(
            embed=panel.preview(),
            view=panel,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tarot.command(
        name="setup",
        description="Customise the reading format, vouch channel and reading log.",
    )
    @app_commands.default_permissions(manage_guild=True)
    @commands.has_permissions(manage_guild=True)
    async def tarot_setup(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()

        view = SetupView(ctx, settings)
        view.message = await ctx.send(
            embed=view.status_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @tarot.command(
        name="count",
        description="How many readings someone has had.",
    )
    @app_commands.describe(user="Who to check. Defaults to you.")
    async def tarot_count(self, ctx, user: discord.Member = None):
        target = user or ctx.author
        total = count_for(ctx.guild.id, target.id)

        if not total:
            await embeds.send(
                ctx,
                embeds.notice(f"{target.display_name} has had no readings yet."),
            )
            return

        await embeds.send(
            ctx,
            embeds.notice(
                f"{target.display_name} has had {total} "
                f"reading{'' if total == 1 else 's'}.",
                title="Readings",
            ),
        )


async def setup(bot):
    await bot.add_cog(Tarot(bot))