"""Test settings for the whole repo: fast PIN hashing and a fixed test pepper (never a real secret)."""

import os

os.environ.setdefault("PIN_HASH_ITERATIONS", "1000")
os.environ.setdefault("ACCOUNTS_PIN_PEPPER", "test-pepper-not-a-secret")
