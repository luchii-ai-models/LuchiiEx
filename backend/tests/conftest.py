import os
import sys

# Ensure /app/backend is on sys.path so tests can `import engine`, `import exchange_service`, etc.
_HERE = os.path.abspath(os.path.dirname(__file__))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# Load backend/.env so modules that read os.environ at import time (db.py MONGO_URL,
# exchange_service ENCRYPTION_KEY, etc.) have what they need when tests import them.
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BACKEND, ".env"))
    load_dotenv(os.path.join(_BACKEND, "..", "frontend", ".env"))
except Exception:
    pass

