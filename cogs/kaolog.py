import discord

import attach
import embeds
from storage import Store

MAX_CHARS = 2000

_store = Store("kaosend_config.json")
config = _store.load()


def save_config():
    _store.save(config)


def get_log_channel_id(guild_id):
    return (config.get(str(guild_id)) or {}).get("log_channel_id")


def set_log_channel(guild_id, channel_id):
    config.setdefault(str(guild_id), {})["log_channel_id"] = channel_id
    save_config()


async def collect(attachments):
    return await attach.read_files([a for a in attachments if a is not None])


def payload(pictures):
    first = next((p for p in pictures if p.is_image), None)
    return attach.attachment_payload(pictures, first)


def log_embed(action, author, target, content, pictures):
    lines = [
        f"**action** : {action}",
        f"**by** : {author.mention}",
        f"**to** : {target}",
    ]
    if pictures:
        lines.append(f"**files** : {len(pictures)}")
    embed = embeds.build("\n".join(lines), title="Kao message sent")
    embed.add_field(name="Content", value=(content or "-")[:1024], inline=False)
    embed.timestamp = discord.utils.utcnow()
    return embed


async def send_log(guild, embed, files=None):
    channel_id = get_log_channel_id(guild.id)
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None or not channel.permissions_for(guild.me).send_messages:
        return
    kwargs = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
    if files:
        kwargs["files"] = files
    try:
        await channel.send(**kwargs)
    except (discord.Forbidden, discord.HTTPException):
        pass