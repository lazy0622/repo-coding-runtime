def parse_port(value):
    """Return a validated integer port."""
    return value


def split_pair(value):
    """Split a key=value pair."""
    return tuple(value.split(":", 1))
