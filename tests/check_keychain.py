"""macOS CI: save a dummy key in the login Keychain, read it back, delete it. Never touches a real key."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rickwire import vault  # noqa: E402

NAME, VALUE = "ci_dummy", "not-a-real-key-1234"
assert vault.BACKEND == "the macOS Keychain", vault.BACKEND
vault.put(NAME, VALUE)
assert vault.get(NAME) == VALUE, "read back failed"
vault.delete(NAME)
assert vault.get(NAME) is None, "delete failed"
print("keychain OK:", vault.BACKEND)
