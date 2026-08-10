from app.text import normalize_name, slugify


assert normalize_name("  Alice Smith  ") == "alice smith"
assert normalize_name("BOB") == "bob"
assert slugify("  Hello   Runtime  ") == "hello-runtime"
