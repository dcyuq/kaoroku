import datetime
import logging
import time

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

_config_store = Store("economy_config.json")
_wallet_store = Store("wallets.json")

config = _config_store.load()
wallets = _wallet_store.load()

TEMPLATE_LIMIT = 3800
CURRENCY_LIMIT = 40
ITEM_LIMIT = 200
MAX_AMOUNT = 1_000_000_000

PAD = "ㅤ"

DEFAULT_ADD = (
    f":03dc_cake:{PAD}balance added!\n"
    f"{PAD}\n"
    f":shortcake1:{PAD}user: {{user}}\n"
    f":strawberri:{PAD}added: {{amount}} {{currency}}\n"
    f":IceCreamSundae:{PAD}new balance: {{balance}} {{currency}}\n"
    f":dndexl:{PAD}by {{moderator}} · {{date}}"
)

DEFAULT_DEDUCT = (
    f":03dc_cake:{PAD}balance deducted.\n"
    f"{PAD}\n"
    f":shortcake1:{PAD}user: {{user}}\n"
    f":strawberri:{PAD}removed: {{amount}} {{currency}}\n"
    f":IceCreamSundae:{PAD}new balance: {{balance}} {{currency}}\n"
    f":dndexl:{PAD}by {{moderator}} · {{date}}"
)

DEFAULT_PAY = (
    f":03dc_cake:{PAD}a new order!\n"
    f"{PAD}\n"
    f":shortcake1:{PAD}buyer: {{user}}\n"
    f":strawberri:{PAD}item: {{item}}\n"
    f":IceCreamSundae:{PAD}spent: {{amount}} {{currency}}\n"
    f":dndexl:{PAD}balance left: {{balance}} {{currency}} · {{date}}"
)

FIELDS = ("user", "amount", "balance", "currency", "item", "moderator",
          "date", "time", "when")

ALIASES = {
    "user": "user", "member": "user", "buyer": "user", "them": "user",
    "customer": "user", "for": "user",
    "amount": "amount", "cookies": "amount", "value": "amount", "number": "amount",
    "balance": "balance", "wallet": "balance", "total": "balance", "left": "balance",
    "currency": "currency", "name": "currency", "unit": "currency",
    "item": "item", "order": "item", "thing": "item", "product": "item",
    "moderator": "moderator", "mod": "moderator", "staff": "moderator",
    "by": "moderator", "author": "moderator",
    "date": "date", "day": "date",
    "time": "time",
    "when": "when", "posted": "when",
}

SAMPLE = {
    "user": "@frost",
    "amount": "50",
    "balance": "250",
    "currency": "cookies",
    "item": "iced latte",
    "moderator": "@staff",
    "date": "august 30, 2026",
    "time": "10:14 am",
    "when": "just now",
}


def save_config():
    _config_store.save(config)


def save_wallets():
    _wallet_store.save(wallets)


def get_config(guild_id):
    return config.get(str(guild_id))


def defaults():
    return {
        "channel_id": None,
        "currency": "cookies",
        "allowed_roles": [],
        "add_template": DEFAULT_ADD,
        "deduct_template": DEFAULT_DEDUCT,
        "pay_template": DEFAULT_PAY,
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


def currency_of(guild_id):
    return settings_for(guild_id).get("currency", "cookies")


def balance_of(guild_id, user_id):
    return int(wallets.get(str(guild_id), {}).get(str(user_id), 0))


def set_balance(guild_id, user_id, value):
    block = wallets.setdefault(str(guild_id), {})
    block[str(user_id)] = max(0, int(value))
    save_wallets()
    return block[str(user_id)]


def wallet_count(guild_id):
    return sum(1 for v in wallets.get(str(guild_id), {}).values() if v)


def stamp_values(guild, stamp):
    stamp = int(stamp or 0)
    if not stamp:
        return {"date": "", "time": "", "when": ""}
    moment = datetime.datetime.fromtimestamp(stamp, tz_for(guild.id))
    return {
        "date": moment.strftime("%B %d, %Y").lower(),
        "time": moment.strftime("%I:%M %p").lstrip("0").lower(),
        "when": f"<t:{stamp}:R>",
    }


def render(template, values, guild):
    return templating.render(template, values, ALIASES, guild)


def event_embed(guild, template, values):
    base = {"date": "", "time": "", "when": ""}
    base.update(values)
    base.update(stamp_values(guild, int(time.time())))
    body = render(template, base, guild)
    return embeds.build(body[:4096])


class CurrencyModal(discord.ui.Modal, title="Currency name"):
    def __init__(self, builder):
        super().__init__()
        self.builder = builder
        self.field = discord.ui.TextInput(
            label="What one unit is called",
            default=builder.settings.get("currency", "cookies"),
            max_length=CURRENCY_LIMIT,
            required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction):
        value = " ".join(self.field.value.split())
        if not value:
            await interaction.response.send_message(
                embed=embeds.error("give it a name."), ephemeral=True
            )
            return
        await interaction.response.defer()
        self.builder.settings["currency"] = value[:CURRENCY_LIMIT]
        save_config()
        await self.builder.refresh()


class FormatModal(discord.ui.Modal):
    def __init__(self, builder, key, label):
        super().__init__(title=label)
        self.builder = builder
        self.key = key
        self.field = discord.ui.TextInput(
            label="Format",
            default=builder.settings[key][:4000],
            style=discord.TextStyle.paragraph,
            max_length=4000,
            required=True,
        )
        self.add_item(self.field)

    async def on_submit(self, interaction):
        text = self.field.value.strip()
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
        self.builder.settings[self.key] = text
        save_config()
        await self.builder.refresh()

        dead = emojiutils.unresolved_names(text, interaction.guild)
        missed = templating.unknown(text, ALIASES)
        notes = []
        if missed:
            notes.append(
                ", ".join(f"`{{{u}}}`" for u in missed)
                + " is not a field, so it prints as written. fields: "
                + ", ".join(f"`{{{f}}}`" for f in FIELDS) + "."
            )
        if dead:
            notes.append(
                ", ".join(f"`:{d}:`" for d in dead)
                + " does not match a server emoji, so it prints as text."
            )
        if notes:
            await interaction.followup.send(
                embed=embeds.error("\n\n".join(notes), title="Check the format"),
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
        placeholder="Where balance changes are logged",
        row=0,
    )
    async def pick(self, interaction, select):
        await interaction.response.defer()
        self.builder.settings["channel_id"] = select.values[0].id
        save_config()
        await self.builder.refresh()


class RoleView(discord.ui.View):
    def __init__(self, builder):
        super().__init__(timeout=300)
        self.builder = builder

    async def interaction_check(self, interaction):
        return interaction.user.id == self.builder.ctx.author.id

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Roles allowed to add or deduct",
        min_values=0,
        max_values=25,
        row=0,
    )
    async def pick(self, interaction, select):
        await interaction.response.defer()
        self.builder.settings["allowed_roles"] = [r.id for r in select.values]
        save_config()
        await self.builder.refresh()


class SetupView(discord.ui.View):
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
        roles = settings.get("allowed_roles", [])
        role_text = ", ".join(f"<@&{r}>" for r in roles) if roles else "none"

        lines = [
            f"**Logs drop in** - {channel.mention if channel else 'not set'}",
            f"**Currency** - {settings.get('currency', 'cookies')}",
            f"**Extra roles that can add/deduct** - {role_text}",
            "",
            f"**Wallets in use** - {wallet_count(guild.id)}",
        ]

        embed = embeds.build("\n".join(lines), title="Balance setup")
        embed.add_field(
            name="Add-log preview",
            value=render(settings["add_template"], SAMPLE, guild)[:1024],
            inline=False,
        )
        embed.set_footer(text="channel · currency · roles · formats — below")
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
            embed=embeds.notice("pick where balance changes are logged."),
            view=ChannelView(self),
            ephemeral=True,
        )

    @discord.ui.button(label="currency", style=discord.ButtonStyle.secondary, row=0)
    async def currency(self, interaction, button):
        await interaction.response.send_modal(CurrencyModal(self))

    @discord.ui.button(label="roles", style=discord.ButtonStyle.secondary, row=0)
    async def roles(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(
                "pick roles that may add or deduct, even without manage server."
            ),
            view=RoleView(self),
            ephemeral=True,
        )

    @discord.ui.button(label="add text", style=discord.ButtonStyle.secondary, row=1)
    async def add_text(self, interaction, button):
        await interaction.response.send_modal(
            FormatModal(self, "add_template", "Add-balance format")
        )

    @discord.ui.button(label="deduct text", style=discord.ButtonStyle.secondary, row=1)
    async def deduct_text(self, interaction, button):
        await interaction.response.send_modal(
            FormatModal(self, "deduct_template", "Deduct format")
        )

    @discord.ui.button(label="pay text", style=discord.ButtonStyle.secondary, row=1)
    async def pay_text(self, interaction, button):
        await interaction.response.send_modal(
            FormatModal(self, "pay_template", "Pay format")
        )

    @discord.ui.button(label="fields", style=discord.ButtonStyle.secondary, row=2)
    async def fields(self, interaction, button):
        await interaction.response.send_message(
            embed=embeds.notice(
                "usable in any format:\n\n"
                + "\n".join(f"`{{{f}}}`" for f in FIELDS)
                + "\n\n`:name:` resolves a server emoji.",
                title="Format fields",
            ),
            ephemeral=True,
        )

    @discord.ui.button(label="reset formats", style=discord.ButtonStyle.danger, row=2)
    async def reset(self, interaction, button):
        await interaction.response.defer()
        self.settings["add_template"] = DEFAULT_ADD
        self.settings["deduct_template"] = DEFAULT_DEDUCT
        self.settings["pay_template"] = DEFAULT_PAY
        save_config()
        await self.refresh()


class Economy(commands.Cog):
    """Cookie wallets: balances, a mod top-up log, and paying with /pay."""

    def __init__(self, bot):
        self.bot = bot

    def can_manage(self, member, settings):
        if member.guild_permissions.manage_guild:
            return True
        allowed = settings.get("allowed_roles", [])
        return any(role.id in allowed for role in getattr(member, "roles", []))

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(ctx, embeds.error("this only works in a server."))
        elif isinstance(error, commands.MissingRequiredArgument):
            if getattr(error, "param", None) and error.param.name == "receipt":
                await embeds.send(
                    ctx,
                    embeds.error(
                        "attach the receipt image. it is required.",
                        title="Receipt needed",
                    ),
                )
            else:
                await embeds.send(
                    ctx, embeds.error("check the arguments and try again.", title="Bad arguments")
                )
        elif isinstance(error, commands.MissingPermissions):
            await embeds.send(
                ctx,
                embeds.error("you do not have permission to use that.", title="Not allowed"),
            )
        elif isinstance(error, (commands.BadArgument, commands.MemberNotFound, commands.RoleNotFound)):
            await embeds.send(ctx, embeds.error("i could not read that."))
        elif isinstance(error, commands.CommandOnCooldown):
            await embeds.send(
                ctx,
                embeds.error(f"try again in {error.retry_after:.0f}s.", title="Slow down"),
            )
        else:
            log.exception("Unhandled error in %s", ctx.command, exc_info=error)
            await embeds.send(
                ctx, embeds.error("something broke on my end. it has been logged.")
            )

    @commands.hybrid_group(
        name="balance",
        aliases=["bal", "wallet"],
        invoke_without_command=True,
        fallback="show",
        description="Check a wallet.",
    )
    @app_commands.describe(user="Whose wallet to check. Defaults to you.")
    @commands.guild_only()
    async def balance(self, ctx, user: discord.Member = None):
        target = user or ctx.author
        amount = balance_of(ctx.guild.id, target.id)
        currency = currency_of(ctx.guild.id)
        who = "you have" if target.id == ctx.author.id else f"{target.display_name} has"
        await embeds.send(
            ctx,
            embeds.build(f"{who} **{amount}** {currency}.", title="Wallet"),
        )

    @balance.command(name="setup", description="Set up the balance log and formats.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def balance_setup(self, ctx):
        settings = ensure_config(ctx.guild.id)
        save_config()
        view = SetupView(ctx, settings)
        view.message = await ctx.send(
            embed=view.status_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @balance.command(name="add", description="Add balance to a user (receipt required).")
    @app_commands.describe(
        user="Who to credit",
        amount="How many to add",
        receipt="Proof of payment (required)",
    )
    @commands.guild_only()
    async def balance_add(
        self, ctx, user: discord.Member, amount: int, receipt: discord.Attachment
    ):
        await ctx.defer(ephemeral=True)
        settings = settings_for(ctx.guild.id)

        if not self.can_manage(ctx.author, settings):
            await embeds.send(ctx, embeds.error("you cannot add balances.", title="Not allowed"))
            return
        if user.bot:
            await embeds.send(ctx, embeds.error("bots do not hold wallets."))
            return
        if amount <= 0 or amount > MAX_AMOUNT:
            await embeds.send(ctx, embeds.error("give a positive amount."))
            return

        channel = ctx.guild.get_channel(settings.get("channel_id"))
        if channel is None:
            await embeds.send(
                ctx,
                embeds.error(
                    f"no log channel set. run `{display_prefix(ctx)}balance setup`.",
                    title="Not set up",
                ),
            )
            return

        picture, problem = await attach.read_image(receipt)
        if problem or picture is None:
            await embeds.send(ctx, embeds.error(problem or "attach the receipt image."))
            return

        currency = settings.get("currency", "cookies")
        new_balance = set_balance(ctx.guild.id, user.id, balance_of(ctx.guild.id, user.id) + amount)

        embed = event_embed(
            ctx.guild,
            settings["add_template"],
            {
                "user": user.mention,
                "amount": str(amount),
                "balance": str(new_balance),
                "currency": currency,
                "moderator": ctx.author.mention,
            },
        )
        embed.set_image(url=picture.reference)

        try:
            sent = await channel.send(embed=embed, file=picture.file(),
                                      allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            await embeds.send(ctx, embeds.error("i cannot post in the log channel."))
            return
        except discord.HTTPException:
            log.exception("balance add log rejected in %s", channel.id)
            await embeds.send(ctx, embeds.error("discord turned that log down."))
            return

        await embeds.send(
            ctx,
            embeds.notice(
                f"added {amount} {currency} to {user.display_name}. "
                f"new balance: {new_balance}. {sent.jump_url}",
                title="Balance added",
            ),
            ephemeral=True,
        )

    @balance.command(name="deduct", aliases=["remove"], description="Remove balance from a user.")
    @app_commands.describe(user="Who to charge", amount="How many to remove")
    @commands.guild_only()
    async def balance_deduct(self, ctx, user: discord.Member, amount: int):
        settings = settings_for(ctx.guild.id)

        if not self.can_manage(ctx.author, settings):
            await embeds.send(ctx, embeds.error("you cannot deduct balances.", title="Not allowed"))
            return
        if user.bot:
            await embeds.send(ctx, embeds.error("bots do not hold wallets."))
            return
        if amount <= 0 or amount > MAX_AMOUNT:
            await embeds.send(ctx, embeds.error("give a positive amount."))
            return

        channel = ctx.guild.get_channel(settings.get("channel_id"))
        if channel is None:
            await embeds.send(
                ctx,
                embeds.error(
                    f"no log channel set. run `{display_prefix(ctx)}balance setup`.",
                    title="Not set up",
                ),
            )
            return

        currency = settings.get("currency", "cookies")
        current = balance_of(ctx.guild.id, user.id)
        removed = min(amount, current)
        new_balance = set_balance(ctx.guild.id, user.id, current - removed)

        embed = event_embed(
            ctx.guild,
            settings["deduct_template"],
            {
                "user": user.mention,
                "amount": str(removed),
                "balance": str(new_balance),
                "currency": currency,
                "moderator": ctx.author.mention,
            },
        )
        try:
            sent = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            sent = None

        note = f"removed {removed} {currency} from {user.display_name}. new balance: {new_balance}."
        if removed < amount:
            note += f" (they only had {current}.)"
        if sent:
            note += f" {sent.jump_url}"
        await embeds.send(ctx, embeds.notice(note, title="Balance deducted"), ephemeral=True)

    @balance.command(name="role", description="Toggle a role that may add or deduct.")
    @app_commands.describe(role="Role to toggle. Leave empty to list them.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def balance_role(self, ctx, role: discord.Role = None):
        settings = ensure_config(ctx.guild.id)
        allowed = settings["allowed_roles"]

        if role is None:
            if not allowed:
                await embeds.send(ctx, embeds.notice("no extra roles can add or deduct yet."))
                return
            listed = ", ".join(f"<@&{r}>" for r in allowed)
            await embeds.send(ctx, embeds.build(listed, title="Balance roles"))
            return

        if role.id in allowed:
            allowed.remove(role.id)
            save_config()
            await embeds.send(ctx, embeds.notice(f"{role.mention} can no longer add or deduct.", title="Updated"))
        else:
            allowed.append(role.id)
            save_config()
            await embeds.send(ctx, embeds.notice(f"{role.mention} can now add or deduct.", title="Updated"))

    @commands.hybrid_command(name="pay", description="Pay for something with your balance.")
    @app_commands.describe(item="What you are paying for", amount="How much to pay")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def pay(self, ctx, item: str, amount: int):
        settings = settings_for(ctx.guild.id)
        currency = settings.get("currency", "cookies")

        thing = " ".join(item.split())[:ITEM_LIMIT]
        if not thing:
            await embeds.send(ctx, embeds.error("say what you are paying for."))
            return
        if amount <= 0 or amount > MAX_AMOUNT:
            await embeds.send(ctx, embeds.error("give a positive amount."))
            return

        current = balance_of(ctx.guild.id, ctx.author.id)
        if current < amount:
            await embeds.send(
                ctx,
                embeds.error(
                    f"not enough {currency}. you have {current}, that costs {amount}.",
                    title="Too poor",
                ),
            )
            return

        new_balance = set_balance(ctx.guild.id, ctx.author.id, current - amount)

        channel = ctx.guild.get_channel(settings.get("channel_id"))
        if channel is not None:
            embed = event_embed(
                ctx.guild,
                settings["pay_template"],
                {
                    "user": ctx.author.mention,
                    "amount": str(amount),
                    "balance": str(new_balance),
                    "currency": currency,
                    "item": thing,
                    "moderator": ctx.author.mention,
                },
            )
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

        await embeds.send(
            ctx,
            embeds.notice(
                f"paid {amount} {currency} for {thing}. balance left: {new_balance}.",
                title="Paid",
            ),
        )


async def setup(bot):
    await bot.add_cog(Economy(bot))