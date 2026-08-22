import discord

ACCENT = discord.Color(0xFFD1DC)

_installed = False


def install():
    """Make ACCENT the default colour for every embed in the process.

    Call this once at startup, before the cogs load. Any embed built without
    an explicit colour comes out themed, whether or not the cog that built it
    remembered to use this module. Passing a colour still wins, which is what
    user built embeds will do later on.
    """
    global _installed
    if _installed:
        return

    original = discord.Embed.__init__

    def patched(self, *, colour=None, color=None, **kwargs):
        if colour is None and color is None:
            colour = ACCENT
        original(self, colour=colour, color=color, **kwargs)

    discord.Embed.__init__ = patched
    _installed = True


def build(description=None, *, title=None, color=None, **kwargs):
    """Every embed the bot sends on its own behalf goes through here.

    The colour is fixed to ACCENT unless a caller passes one explicitly.
    User built embeds (say, embed, and friends) are the only thing that
    should ever pass `color`, so their look stays configurable later on.
    """
    return discord.Embed(
        title=title,
        description=description,
        color=ACCENT if color is None else color,
        **kwargs,
    )


def notice(description, *, title=None, **kwargs):
    """A neutral or successful result."""
    return build(description, title=title, **kwargs)


def error(description, *, title="Error", **kwargs):
    """Something the user needs to fix or be told about."""
    return build(description, title=title, **kwargs)


async def send(messageable, embed, **kwargs):
    """Send an embed, degrading to plain text if embeds are not permitted."""
    kwargs.setdefault("allowed_mentions", discord.AllowedMentions.none())

    try:
        return await messageable.send(embed=embed, **kwargs)
    except discord.Forbidden:
        pass
    except discord.HTTPException:
        return None

    fallback = "\n".join(part for part in (embed.title, embed.description) if part)
    if not fallback:
        return None

    try:
        return await messageable.send(fallback[:2000], **kwargs)
    except discord.HTTPException:
        return None