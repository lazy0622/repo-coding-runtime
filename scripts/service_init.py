"""Initialize only this service's PostgreSQL tables."""

from pico.service.config import Settings
from pico.service.store import Store

settings = Settings.load()
store = Store(settings.database_url)
store.initialize()
store.engine.dispose()
print("Runtime service database initialized")
