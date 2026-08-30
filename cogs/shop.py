import logging
import uuid

import discord
from discord import app_commands
from discord.ext import commands

import embeds
from prefixes import display_prefix
from storage import Store
from cogs import economy

log = logging.getLogger(__name__)

_shop_store = Store("shop.json")
shop = _shop_store.load()

NAME_LIMIT = 80
DESC_LIMIT = 300
MAX_ITEMS = 200
MAX_PRICE = 1_000_000_000


def save_shop():
    _shop_store.save(shop)


def guild_items(guild_id):
    return shop.setdefault(str(guild_id), [])


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
    if stock < 0:
        return "∞"
    return str(stock)


class Shop(commands.Cog):
    """A marketplace to spend the currency on staff-listed items."""

    def __init__(self, bot):
        self.bot = bot

    async def cog_command_error(self, ctx, error):
        error = getattr(error, "original", error)
        if isinstance(error, commands.NoPrivateMessage):
            await embeds.send(ctx, embeds.error("this only works in a server."))
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
                    f"the shop is empty. staff can add items with "
                    f"`{display_prefix(ctx)}shop add <price> <name>`.",
                    title="Shop",
                ),
            )
            return

        lines = []
        for item in items:
            line = f"`{item['id']}` **{item['name']}** — {item['price']} {currency} · stock {stock_text(item)}"
            if item.get("desc"):
                line += f"\n{item['desc']}"
            lines.append(line)

        await embeds.send(
            ctx,
            embeds.build(
                "\n\n".join(lines)[:4000]
                + f"\n\nbuy with `{display_prefix(ctx)}buy <item> [qty]`.",
                title="Shop",
            ),
        )

    @shop.command(name="add", description="Add an item to the shop.")
    @app_commands.describe(price="Cost in the currency", name="Item name")
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

        item = {
            "id": uuid.uuid4().hex[:6],
            "name": title,
            "price": int(price),
            "desc": "",
            "stock": -1,
        }
        items.append(item)
        save_shop()

        currency = economy.currency_of(ctx.guild.id)
        await embeds.send(
            ctx,
            embeds.notice(
                f"added **{title}** for {price} {currency} (`{item['id']}`).",
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
        await embeds.send(ctx, embeds.notice(f"removed **{found['name']}**.", title="Removed"))

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
        await embeds.send(ctx, embeds.notice(f"**{found['name']}** now costs {price} {currency}.", title="Updated"))

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
        await embeds.send(ctx, embeds.notice(f"**{found['name']}** stock is now {stock_text(found)}.", title="Updated"))

    @shop.command(name="desc", aliases=["description"], description="Set an item's description.")
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
        await embeds.send(ctx, embeds.notice(f"updated **{found['name']}**.", title="Updated"))

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
            await embeds.send(ctx, embeds.error(f"only {stock} left of **{found['name']}**.", title="Out of stock"))
            return

        currency = economy.currency_of(ctx.guild.id)
        total = found["price"] * quantity
        current = economy.balance_of(ctx.guild.id, ctx.author.id)
        if current < total:
            await embeds.send(
                ctx,
                embeds.error(
                    f"not enough {currency}. you have {current}, that costs {total}.",
                    title="Too poor",
                ),
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
                f"bought {quantity}× **{found['name']}** for {total} {currency}. "
                f"balance left: {new_balance}.",
                title="Purchased",
            ),
        )


async def setup(bot):
    await bot.add_cog(Shop(bot))