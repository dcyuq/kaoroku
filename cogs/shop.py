import logging
import uuid

import discord
from discord import app_commands
from discord.ext import commands

import embeds
import emojiutils
import templating
from prefixes import display_prefix
from storage import Store
from cogs import economy

log = logging.getLogger(__name__)

_shop_store = Store("shop.json")
_config_store = Store("shop_config.json")
shop = _shop_store.load()
config = _config_store.load()

NAME_LIMIT = 80
DESC_LIMIT = 300
MAX_ITEMS = 200
MAX_PRICE = 1_000_000_000
TEMPLATE_LIMIT = 2000

DEFAULT_ITEM_FORMAT = "`{id}` **{name}** — {price} {currency} · stock {stock}\n{desc}"

SHOP_FIELDS = ("id", "name", "price", "currency", "stock", "desc")

SHOP_ALIASES = {
    "id": "id", "code": "id",
    "name": "name", "item": "name", "title": "name",
    "price": "price", "cost": "price", "amount": "price",
    "currency": "currency", "unit": "currency",
    "stock": "stock", "left": "stock", "quantity": "stock",
    "desc": "desc", "description": "desc", "note": "desc", "details": "desc",
}


def save_shop():
    _shop_store.save(shop)


def save_config():
    _config_store.save(config)


def guild_items(guild_id):
    return shop.setdefault(str(guild_id), [])


def format_for(guild_id):
    return config.get(str(guild_id), {}).get("item_format", DEFAULT_ITEM_FORMAT)


def set_format(guild_id, value):
    config.setdefault(str(guild_id), {})["item_format"] = value
    save_config()


def norm(text):
    return " ".join((text or "").lower().split())


def find_item(guild_id, key):
    key = norm(key)
    for item in guild_items(guild_id):
        if item["id"] == key or norm(item["name"]) == key:
            return item
    return None


def can_manage(member, guild_id):
    if member.guild_permissions.manage_guild:
        return True
    allowed = economy.settings_for(guild_id).get("allowed_roles", [])
    return any(role.id in allowed for role in getattr(member, "roles", []))


def stock_text(item):
    stock = item.get("stock", -1)
    return "∞" if stock < 0 else str(stock)


def disp(guild, text):
    return emojiutils.resolve_names(text, guild)


def render_item(guild, fmt, item, currency):
    values = {
        "id": item["id"],
        "name": item["name"],
        "price": str(item["price"]),
        "currency": currency,
        "stock": stock_text(item),
        "desc": item.get("desc", ""),
    }
    return templating.render(fmt, values, SHOP_ALIASES, guild).strip()


SAMPLE_ITEM = {"id": "a1b2c3", "name": "iced latte", "price": 50, "desc": "cold and sweet", "stock": -1}


class FormatModal(discord.ui.Modal, title="Item format"):
    def __init__(self, builder):
        super().__init__()
        self.builder = builder
        self.field = discord.ui.TextInput(
            label="How each item is shown",
            default=format_for(builder.ctx.guild.id)[:2000],
            style=discord.TextStyle.paragraph,
            max_length=2000,
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
                embed=embeds.error(f"keep it under {TEMPLATE_LIMIT} characters."), ephemeral=True
            )
            return
        await interaction.response.defer()
        set_format(interaction.guild.id, text)
        await self.builder.refresh()

        missed = templating.unknown(text, SHOP_ALIASES)
        if missed:
            listed = ", ".join(f"`{{{u}}}`" for u in missed)
            await interaction.followup.send(
                embed=embeds.error(
                    f"{listed} is not a field, so it prints as written. fields: "
                    + ", ".join(f"`{{{f}}}`" for f in SHOP_FIELDS) + ".",
                    title="Check the format",
                ),
                ephemeral=True,
            )


class SetupView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=900)
        self.ctx = ctx
        self.message = None

    async def interaction_check(self, interaction):
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                embed=embeds.error("this deck isn't yours.", title="Not yours"), ephemeral=True
            )
            return False
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                embed=embeds.error("you need manage server permission.", title="Not allowed"),
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
        currency = economy.currency_of(guild.id)
        fmt = format_for(guild.id)
        preview = render_item(guild, fmt, SAMPLE_ITEM, currency) or "(empty)"

        embed = embeds.build(
            "this is how each item shows in `shop`. edit it below.",
            title="Shop setup",
        )
        embed.add_field(name="Preview", value=preview[:1024], inline=False)
        embed.add_field(
            name="Fields",
            value=", ".join(f"`{{{f}}}`" for f in SHOP_FIELDS)
            + "\n`:name:` shows a server emoji. put emoji in item names or descriptions too.",
            inline=False,
        )
        return embed

    async def refresh(self):
        if self.message is None:
            return
        try:
            await self.message.edit(embed=self.status_embed(), view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="format", style=discord.ButtonStyle.secondary, row=0)
    async def format_button(self, interaction, button):
        await interaction.response.send_modal(FormatModal(self))

    @discord.ui.button(label="reset", style=discord.ButtonStyle.danger, row=0)
    async def reset(self, interaction, button):
        await interaction.response.defer()
        set_format(interaction.guild.id, DEFAULT_ITEM_FORMAT)
        await self.refresh()


class Shop(commands.Cog):
    """A marketplace to spend the currency on staff-listed items."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(ctx, embeds.error("this only works in a server."))
        elif isinstance(error, commands.MissingPermissions):
            await embeds.send(ctx, embeds.error("you do not have permission.", title="Not allowed"))
        elif isinstance(error, (commands.BadArgument, commands.MissingRequiredArgument)):
            await embeds.send(ctx, embeds.error("check the arguments and try again.", title="Bad arguments"))
        elif isinstance(error, commands.CommandOnCooldown):
            await embeds.send(ctx, embeds.error(f"try again in {error.retry_after:.0f}s.", title="Slow down"))
        else:
            log.exception("Unhandled error in %s", ctx.command, exc_info=error)
            await embeds.send(ctx, embeds.error("something broke on my end. it has been logged."))

    @commands.hybrid_group(
        name="shop",
        aliases=["market", "store"],
        invoke_without_command=True,
        fallback="list",
        description="Browse the marketplace.",
    )
    @commands.guild_only()
    async def shop(self, ctx):
        await self.show_shop(ctx)

    async def show_shop(self, ctx):
        items = guild_items(ctx.guild.id)
        currency = economy.currency_of(ctx.guild.id)

        if not items:
            await embeds.send(
                ctx,
                embeds.notice(
                    f"the shop is empty. staff add items with "
                    f"`{display_prefix(ctx)}shop add <price> <name>`.",
                    title="Shop",
                ),
            )
            return

        fmt = format_for(ctx.guild.id)
        blocks = [render_item(ctx.guild, fmt, item, currency) for item in items]
        body = "\n\n".join(b for b in blocks if b)[:4000]
        body += f"\n\nbuy with `{display_prefix(ctx)}buy <item> [qty]`."

        await embeds.send(ctx, embeds.build(body, title="Shop"))

    @shop.command(name="setup", description="Edit how shop items are presented.")
    @app_commands.default_permissions(manage_guild=True)
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def shop_setup(self, ctx):
        view = SetupView(ctx)
        view.message = await ctx.send(
            embed=view.status_embed(), view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @shop.command(name="add", description="Add an item to the shop.")
    @app_commands.describe(price="Cost in the currency", name="Item name (emoji allowed)")
    @commands.guild_only()
    async def shop_add(self, ctx, price: int, *, name: str):
        if not can_manage(ctx.author, ctx.guild.id):
            await embeds.send(ctx, embeds.error("you cannot manage the shop.", title="Not allowed"))
            return

        title = " ".join(name.split())[:NAME_LIMIT]
        if not title:
            await embeds.send(ctx, embeds.error("give the item a name."))
            return
        if price < 0 or price > MAX_PRICE:
            await embeds.send(ctx, embeds.error("give a valid price."))
            return

        items = guild_items(ctx.guild.id)
        if len(items) >= MAX_ITEMS:
            await embeds.send(ctx, embeds.error(f"the shop is full ({MAX_ITEMS} items)."))
            return
        if find_item(ctx.guild.id, title):
            await embeds.send(ctx, embeds.error("an item with that name already exists."))
            return

        item = {"id": uuid.uuid4().hex[:6], "name": title, "price": int(price), "desc": "", "stock": -1}
        items.append(item)
        save_shop()

        currency = economy.currency_of(ctx.guild.id)
        await embeds.send(
            ctx,
            embeds.notice(
                f"added {disp(ctx.guild, title)} for {price} {currency} (`{item['id']}`).",
                title="Item added",
            ),
        )

    @shop.command(name="remove", aliases=["delete", "del"], description="Remove an item.")
    @app_commands.describe(item="Item name or id")
    @commands.guild_only()
    async def shop_remove(self, ctx, *, item: str):
        if not can_manage(ctx.author, ctx.guild.id):
            await embeds.send(ctx, embeds.error("you cannot manage the shop.", title="Not allowed"))
            return
        found = find_item(ctx.guild.id, item)
        if found is None:
            await embeds.send(ctx, embeds.error("no item like that."))
            return
        guild_items(ctx.guild.id).remove(found)
        save_shop()
        await embeds.send(ctx, embeds.notice(f"removed {disp(ctx.guild, found['name'])}.", title="Removed"))

    @shop.command(name="price", description="Change an item's price.")
    @app_commands.describe(item="Item name or id", price="New price")
    @commands.guild_only()
    async def shop_price(self, ctx, item: str, price: int):
        if not can_manage(ctx.author, ctx.guild.id):
            await embeds.send(ctx, embeds.error("you cannot manage the shop.", title="Not allowed"))
            return
        found = find_item(ctx.guild.id, item)
        if found is None:
            await embeds.send(ctx, embeds.error("no item like that."))
            return
        if price < 0 or price > MAX_PRICE:
            await embeds.send(ctx, embeds.error("give a valid price."))
            return
        found["price"] = int(price)
        save_shop()
        currency = economy.currency_of(ctx.guild.id)
        await embeds.send(ctx, embeds.notice(f"{disp(ctx.guild, found['name'])} now costs {price} {currency}.", title="Updated"))

    @shop.command(name="stock", description="Set an item's stock (-1 for unlimited).")
    @app_commands.describe(item="Item name or id", amount="Stock, or -1 for unlimited")
    @commands.guild_only()
    async def shop_stock(self, ctx, item: str, amount: int):
        if not can_manage(ctx.author, ctx.guild.id):
            await embeds.send(ctx, embeds.error("you cannot manage the shop.", title="Not allowed"))
            return
        found = find_item(ctx.guild.id, item)
        if found is None:
            await embeds.send(ctx, embeds.error("no item like that."))
            return
        found["stock"] = -1 if amount < 0 else int(amount)
        save_shop()
        await embeds.send(ctx, embeds.notice(f"{disp(ctx.guild, found['name'])} stock is now {stock_text(found)}.", title="Updated"))

    @shop.command(name="desc", aliases=["description"], description="Set an item's description (emoji allowed).")
    @app_commands.describe(item="Item name or id", text="Description, or blank to clear")
    @commands.guild_only()
    async def shop_desc(self, ctx, item: str, *, text: str = ""):
        if not can_manage(ctx.author, ctx.guild.id):
            await embeds.send(ctx, embeds.error("you cannot manage the shop.", title="Not allowed"))
            return
        found = find_item(ctx.guild.id, item)
        if found is None:
            await embeds.send(ctx, embeds.error("no item like that."))
            return
        found["desc"] = " ".join(text.split())[:DESC_LIMIT]
        save_shop()
        await embeds.send(ctx, embeds.notice(f"updated {disp(ctx.guild, found['name'])}.", title="Updated"))

    @commands.hybrid_command(name="buy", description="Buy an item from the shop.")
    @app_commands.describe(item="Item name or id", quantity="How many")
    @commands.guild_only()
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def buy(self, ctx, item: str, quantity: int = 1):
        found = find_item(ctx.guild.id, item)
        if found is None:
            await embeds.send(ctx, embeds.error("no item like that in the shop."))
            return
        if quantity < 1 or quantity > 10000:
            await embeds.send(ctx, embeds.error("give a valid quantity."))
            return

        stock = found.get("stock", -1)
        if stock >= 0 and stock < quantity:
            await embeds.send(ctx, embeds.error(f"only {stock} left of {disp(ctx.guild, found['name'])}.", title="Out of stock"))
            return

        currency = economy.currency_of(ctx.guild.id)
        total = found["price"] * quantity
        current = economy.balance_of(ctx.guild.id, ctx.author.id)
        if current < total:
            await embeds.send(
                ctx,
                embeds.error(f"not enough {currency}. you have {current}, that costs {total}.", title="Too poor"),
            )
            return

        new_balance = economy.set_balance(ctx.guild.id, ctx.author.id, current - total)
        if stock >= 0:
            found["stock"] = stock - quantity
            save_shop()

        settings = economy.settings_for(ctx.guild.id)
        channel = ctx.guild.get_channel(settings.get("channel_id"))
        if channel is not None:
            label = found["name"] if quantity == 1 else f"{quantity}× {found['name']}"
            body = economy.render_event(
                ctx.guild,
                settings["pay_template"],
                {
                    "user": ctx.author.mention,
                    "amount": str(total),
                    "balance": str(new_balance),
                    "currency": currency,
                    "item": label,
                    "moderator": ctx.author.mention,
                },
            )
            try:
                if settings.get("embed", True):
                    await channel.send(embed=embeds.build(body[:4096]), allowed_mentions=discord.AllowedMentions.none())
                else:
                    await channel.send(content=body[:2000], allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException:
                pass

        await embeds.send(
            ctx,
            embeds.notice(
                f"bought {quantity}× {disp(ctx.guild, found['name'])} for {total} {currency}. "
                f"balance left: {new_balance}.",
                title="Purchased",
            ),
        )


async def setup(bot):
    await bot.add_cog(Shop(bot))