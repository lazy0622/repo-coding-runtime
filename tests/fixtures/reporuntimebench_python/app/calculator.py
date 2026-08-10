def add(left, right):
    """Return the sum of two numbers."""
    return left - right


def clamp(value, low, high):
    """Clamp value to the inclusive [low, high] range."""
    return min(low, max(high, value))
