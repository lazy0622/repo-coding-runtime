from .config import API_PREFIX, DEFAULT_RETRIES


def retry_count():
    return 1


def users_endpoint():
    return "/users"
