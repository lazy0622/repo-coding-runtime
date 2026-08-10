import re


def normalize_name(value):
    """Trim and lowercase a user-facing name."""
    return value.strip()


def slugify(value):
    """Return a lowercase hyphen-separated slug."""
    return re.sub(r"\s+", "_", value.strip().lower())
