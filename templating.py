import re

import emojiutils
PLACEHOLDER = re.compile(r"\{([^{}\n]{1,60})\}")


def normalize(raw):
    """Fold a written placeholder down to a comparable key."""
    key = raw.strip().lower().lstrip("@").replace("'", "").replace("\u2019", "")
    return re.sub(r"[\s_-]+", " ", key).strip()


def field_for(raw, aliases):
    return aliases.get(normalize(raw))


def render(template, values, aliases, guild):
    """Substitute {field} placeholders, then resolve :emoji: names.

    Unrecognised placeholders are left untouched rather than raising, so a
    typo shows up in the preview instead of breaking a live post.
    """
    def swap(match):
        field = field_for(match.group(1), aliases)
        if field is None:
            return match.group(0)
        return str(values.get(field, match.group(0)))

    return emojiutils.resolve_names(PLACEHOLDER.sub(swap, template), guild)


def unknown(template, aliases):
    """Placeholders in the template that map to no known field."""
    return sorted(
        {
            m.group(1)
            for m in PLACEHOLDER.finditer(template)
            if field_for(m.group(1), aliases) is None
        }
    )