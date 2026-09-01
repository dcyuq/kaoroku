"""Self-contained HTML ticket transcripts, themed to match the bot.

No third party libraries: a transcript is one plain .html file with its
styling inlined, so it opens anywhere and needs nothing hosted. Avatars and
attachments are referenced by their Discord CDN url (a point in time
snapshot; those links are signed and lapse after about a day, which is why
the file is also kept in the log channel and dm'd to the opener).
"""

import datetime
import html
import re

import discord

import embeds
from scheduling import tz_for

ACCENT = f"#{embeds.ACCENT.value:06x}"

CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_~]{2,32}):\d{15,25}>")


def _accent_soft(alpha):
    r, g, b = (embeds.ACCENT.value >> 16) & 0xFF, (embeds.ACCENT.value >> 8) & 0xFF, embeds.ACCENT.value & 0xFF
    return f"rgba({r}, {g}, {b}, {alpha})"


def _stamp(moment, guild_id):
    local = moment.astimezone(tz_for(guild_id))
    return local.strftime("%B %d, %Y · %I:%M %p").replace("· 0", "· ").lower()


def _clean(text):
    text = CUSTOM_EMOJI.sub(r":\1:", text or "")
    text = html.escape(text)
    return text.replace("\n", "<br>")


def _attachment_html(attachment):
    name = html.escape(attachment.filename)
    url = html.escape(attachment.url)
    is_image = (attachment.content_type or "").startswith("image/") or (
        attachment.filename.lower().endswith(
            (".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp")
        )
    )
    if is_image:
        return (
            f'<a class="att" href="{url}" target="_blank" rel="noopener">'
            f'<img loading="lazy" src="{url}" alt="{name}"></a>'
        )
    return f'<a class="file" href="{url}" target="_blank" rel="noopener">📎 {name}</a>'


def _embed_html(embed):
    bits = ['<div class="embed">']
    if embed.author and embed.author.name:
        bits.append(f'<div class="embed-author">{html.escape(embed.author.name)}</div>')
    if embed.title:
        bits.append(f'<div class="embed-title">{html.escape(embed.title)}</div>')
    if embed.description:
        bits.append(f'<div class="embed-desc">{_clean(embed.description)}</div>')
    for field in embed.fields:
        bits.append(
            '<div class="embed-field">'
            f'<div class="embed-field-name">{html.escape(field.name or "")}</div>'
            f'<div class="embed-field-value">{_clean(field.value or "")}</div>'
            "</div>"
        )
    if embed.image and embed.image.url:
        safe = html.escape(embed.image.url)
        bits.append(
            f'<a class="att" href="{safe}" target="_blank" rel="noopener">'
            f'<img loading="lazy" src="{safe}" alt="embed image"></a>'
        )
    bits.append("</div>")
    return "".join(bits)


def _message_html(message, guild_id):
    author = message.author
    name = html.escape(getattr(author, "display_name", None) or str(author))
    avatar = html.escape(author.display_avatar.url)
    stamp = html.escape(_stamp(message.created_at, guild_id))
    bot_tag = '<span class="bot">bot</span>' if getattr(author, "bot", False) else ""

    body = []
    try:
        content = message.clean_content
    except Exception:
        content = message.content
    if content:
        body.append(f'<div class="content">{_clean(content)}</div>')
    for attachment in message.attachments:
        body.append(_attachment_html(attachment))
    for embed in message.embeds:
        body.append(_embed_html(embed))
    if not body:
        body.append('<div class="content muted">[no text content]</div>')

    return (
        '<div class="msg">'
        f'<img class="avatar" loading="lazy" src="{avatar}" alt="">'
        '<div class="msg-body">'
        f'<div class="msg-head"><span class="author">{name}</span>{bot_tag}'
        f'<span class="time">{stamp}</span></div>'
        f'{"".join(body)}'
        "</div></div>"
    )


def _meta_row(label, value):
    return (
        f'<div class="meta-row"><span class="meta-label">{html.escape(label)}</span>'
        f'<span class="meta-value">{value}</span></div>'
    )


def build_html(guild, channel, entry, messages):
    """Return a full HTML document (str) for a closed ticket."""
    number = entry.get("number", 0)
    kind = html.escape(entry.get("kind", "Ticket"))
    guild_name = html.escape(guild.name)
    channel_name = html.escape(getattr(channel, "name", f"ticket-{number:04d}"))

    opener = guild.get_member(entry["opener_id"])
    closer = guild.get_member(entry.get("closer_id") or 0)
    claimer = guild.get_member(entry.get("claimed_by") or 0)

    def who(member, fallback_id):
        if member:
            return html.escape(member.display_name)
        return f"user {fallback_id}" if fallback_id else "unknown"

    opened = datetime.datetime.fromtimestamp(
        entry["opened_at"], datetime.timezone.utc
    )
    closed = datetime.datetime.fromtimestamp(
        entry.get("closed_at") or entry["opened_at"], datetime.timezone.utc
    )

    meta = [
        _meta_row("ticket", f"#{number:04d} · {kind}"),
        _meta_row("channel", f"#{channel_name}"),
        _meta_row("opened by", who(opener, entry["opener_id"])),
        _meta_row("closed by", who(closer, entry.get("closer_id"))),
        _meta_row(
            "claimed by",
            who(claimer, entry.get("claimed_by")) if entry.get("claimed_by") else "not claimed",
        ),
        _meta_row("opened", html.escape(_stamp(opened, guild.id))),
        _meta_row("closed", html.escape(_stamp(closed, guild.id))),
        _meta_row("messages", str(entry.get("total_msgs", len(messages)))),
    ]
    if entry.get("reason"):
        meta.append(_meta_row("reason", _clean(entry["reason"])))

    for question, answer in entry.get("answers", []):
        meta.append(_meta_row(question[:120], _clean((answer or "-")[:400])))

    rendered = "".join(_message_html(m, guild.id) for m in messages)
    if not rendered:
        rendered = '<div class="empty">no messages were recorded in this ticket.</div>'

    css = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
body {
  margin: 0; padding: 24px 14px 48px;
  font: 15px/1.55 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #33262b; background: #fff7fa;
}
.wrap { max-width: 820px; margin: 0 auto; }
.head {
  background: %(accent)s; color: #4a2b36;
  border-radius: 18px; padding: 20px 22px; margin-bottom: 18px;
  box-shadow: 0 6px 20px %(soft)s;
}
.head h1 { margin: 0 0 2px; font-size: 20px; }
.head .sub { margin: 0; font-size: 13px; opacity: .8; }
.meta {
  background: #fff; border: 1px solid %(soft)s; border-radius: 16px;
  padding: 14px 18px; margin-bottom: 22px;
}
.meta-row { display: flex; gap: 12px; padding: 4px 0; font-size: 14px; }
.meta-label {
  flex: 0 0 110px; text-transform: lowercase; color: #a67; font-weight: 600;
}
.meta-value { flex: 1; word-break: break-word; }
.msg { display: flex; gap: 12px; padding: 10px 6px; border-radius: 12px; }
.msg:hover { background: #fff; }
.avatar { width: 40px; height: 40px; border-radius: 50%%; flex: 0 0 40px; object-fit: cover; }
.msg-body { min-width: 0; flex: 1; }
.msg-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.author { font-weight: 600; color: #b25c78; }
.bot {
  font-size: 10px; text-transform: uppercase; letter-spacing: .04em;
  background: %(accent)s; color: #4a2b36; padding: 1px 6px; border-radius: 6px;
}
.time { font-size: 12px; color: #b299a2; }
.content { white-space: normal; word-wrap: break-word; margin-top: 2px; }
.muted { color: #b299a2; font-style: italic; }
.att img { max-width: 320px; max-height: 320px; border-radius: 10px; margin-top: 6px; display: block; }
.file {
  display: inline-block; margin-top: 6px; padding: 6px 10px;
  background: #fff; border: 1px solid %(soft)s; border-radius: 8px;
  color: #b25c78; text-decoration: none;
}
.embed {
  margin-top: 6px; padding: 10px 12px; border-left: 4px solid %(accent)s;
  background: #fff; border-radius: 8px; max-width: 460px;
}
.embed-author { font-size: 12px; color: #a67; }
.embed-title { font-weight: 600; margin-bottom: 2px; }
.embed-desc { font-size: 14px; }
.embed-field { margin-top: 6px; }
.embed-field-name { font-weight: 600; font-size: 13px; }
.embed-field-value { font-size: 13px; }
.empty, .foot { text-align: center; color: #b299a2; padding: 24px; font-size: 13px; }
.foot { padding-top: 30px; }
a { color: #b25c78; }
@media (prefers-color-scheme: dark) {
  body { background: #1c1418; color: #ece3e7; }
  .meta, .msg:hover, .file, .embed { background: #26191f; }
  .meta, .file, .embed { border-color: #3a2830; }
  .author { color: #ffb6cb; }
  .time, .muted, .meta-value, .empty, .foot { color: #b9a4ad; }
  .content, .embed-desc { color: #ece3e7; }
  .meta-label { color: #d59bad; }
}
""" % {"accent": ACCENT, "soft": _accent_soft(0.35)}

    return (
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>ticket-{number:04d} transcript</title><style>{css}</style>"
        "</head><body><div class=\"wrap\">"
        f'<div class="head"><h1>ticket-{number:04d} · {kind}</h1>'
        f'<p class="sub">{guild_name} · #{channel_name}</p></div>'
        f'<div class="meta">{"".join(meta)}</div>'
        f'<div class="log">{rendered}</div>'
        '<div class="foot">transcript generated by kaoroku 🍰</div>'
        "</div></body></html>"
    )