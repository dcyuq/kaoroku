import re

import discord

CUSTOM_EMOJI = re.compile(r"^<a?:[A-Za-z0-9_]{2,32}:\d{15,25}>$")

MAX_UNICODE_LENGTH = 8


def valid_shape(text):
    """Shape check that needs no guild, so stored values can be re-tested."""
    if not text:
        return False
    if text.startswith("<"):
        return bool(CUSTOM_EMOJI.match(text))
    return len(text) <= MAX_UNICODE_LENGTH and any(
        ord(ch) >= 0x2000 for ch in text
    )


def parse(raw, guild):
    """Validate an icon. Returns (stored, error). Blank means no icon.

    Custom emoji have to come from a server the bot shares, otherwise
    Discord rejects the button when it is used rather than when it is set.
    """
    text = (raw or "").strip()

    if not text:
        return None, None

    if text.startswith("<"):
        if not CUSTOM_EMOJI.match(text):
            return None, (
                "that custom emoji is not in a form i can read. type a "
                "backslash before it and paste what discord gives you, "
                "like `<:name:123456789012345678>`."
            )
        emoji_id = int(text.rstrip(">").rsplit(":", 1)[-1])
        if guild.get_emoji(emoji_id) is None:
            return None, (
                "i cannot use that emoji. it has to be from a server i am "
                "also in."
            )
        return text, None

    if len(text) > MAX_UNICODE_LENGTH:
        return None, "that is too long for an icon. use one emoji."

    if not valid_shape(text):
        return None, (
            "that is not an emoji. paste one, or leave the field blank for "
            "no icon."
        )

    return text, None


def to_partial(raw):
    """Never let a stale or hand-edited icon stop a view from rendering."""
    if not valid_shape(raw):
        return None
    try:
        return discord.PartialEmoji.from_str(raw)
    except (ValueError, TypeError):
        return None


SHORTCODE = re.compile(r":([A-Za-z0-9_]{2,32}(?:~\d+)?):")

RESOLVED = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d+>")


def find_named(guild, name):
    """Look up an emoji by shortcode name.

    Discord's picker shows `name~2` when several emoji share a name, so the
    suffix is stripped before matching. Matching is case-insensitive because
    what people type rarely matches the stored casing.
    """
    if guild is None:
        return None

    base = re.sub(r"~\d+$", "", name).lower()
    for emoji in guild.emojis:
        if emoji.name.lower() == base:
            return emoji
    return None


def resolve_names(text, guild):
    """Turn :name: into a usable custom emoji, leaving unknown names alone.

    Already-resolved <:name:id> forms are protected first so they don't get
    mangled by the second pass.
    """
    if not text or guild is None:
        return text

    kept = []

    def stash(match):
        kept.append(match.group(0))
        return f"\x00{len(kept) - 1}\x00"

    text = RESOLVED.sub(stash, text)

    def swap(match):
        found = find_named(guild, match.group(1))
        return str(found) if found else match.group(0)

    text = SHORTCODE.sub(swap, text)

    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], text)


def unresolved_names(text, guild):
    """Shortcodes in the text that this server has no emoji for."""
    if not text or guild is None:
        return []

    stripped = RESOLVED.sub("", text)
    missing = [
        name
        for name in SHORTCODE.findall(stripped)
        if find_named(guild, name) is None
    ]
    return sorted(dict.fromkeys(missing))