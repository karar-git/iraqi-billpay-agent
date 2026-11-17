"""Settings of the Instagram channel, read from the environment once, when the app is made.

The secrets (the access token, the app secret, the verify token) come only from the environment. They are never
logged and never part of repr(Settings).
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass, field

from channels.instagram.client import DEFAULT_BASE, DEFAULT_VERSION, api_base, api_version
from channels.instagram.link import GLOBAL_WRONG_PER_HOUR

# What a health check reports as missing when it is empty (names only, never values).
REQUIRED = ("INSTAGRAM_TOKEN", "INSTAGRAM_APP_SECRET", "INSTAGRAM_VERIFY_TOKEN", "INSTAGRAM_ACCOUNT_ID")


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)  # INSTAGRAM_TOKEN: the account's access token, sent only to api_base
    app_secret: str = field(default="", repr=False)  # INSTAGRAM_APP_SECRET: checks X-Hub-Signature-256
    verify_token: str = field(default="", repr=False)  # INSTAGRAM_VERIFY_TOKEN: the webhook handshake
    account_id: str = ""  # INSTAGRAM_ACCOUNT_ID: the professional account this channel answers for
    api_base: str = DEFAULT_BASE  # INSTAGRAM_API_BASE: graph.instagram.com, or graph.facebook.com (Facebook Login)
    api_version: str = DEFAULT_VERSION  # INSTAGRAM_API_VERSION
    # INSTAGRAM_LINK_FLOOD_PER_HOUR: wrong link codes from all senders in an hour before the brake is on (0: off).
    link_flood_per_hour: int = GLOBAL_WRONG_PER_HOUR

    def __post_init__(self) -> None:
        # Refused at start-up: a typo in the host must never send the token somewhere else.
        object.__setattr__(self, "api_base", api_base(self.api_base))
        object.__setattr__(self, "api_version", api_version(self.api_version))
        if self.account_id and not (self.account_id.isascii() and self.account_id.isdigit()):
            raise ValueError("INSTAGRAM_ACCOUNT_ID must be the account's numeric id")
        if not isinstance(self.link_flood_per_hour, int) or self.link_flood_per_hour < 0:
            raise ValueError("INSTAGRAM_LINK_FLOOD_PER_HOUR must be a whole number, 0 or more")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return (env.get(name) or "").strip() or default

        return cls(
            token=get("INSTAGRAM_TOKEN"),
            app_secret=get("INSTAGRAM_APP_SECRET"),
            verify_token=get("INSTAGRAM_VERIFY_TOKEN"),
            account_id=get("INSTAGRAM_ACCOUNT_ID"),
            api_base=get("INSTAGRAM_API_BASE", DEFAULT_BASE),
            api_version=get("INSTAGRAM_API_VERSION", DEFAULT_VERSION),
            link_flood_per_hour=_whole(get("INSTAGRAM_LINK_FLOOD_PER_HOUR"), GLOBAL_WRONG_PER_HOUR),
        )

    @property
    def missing(self) -> list[str]:
        """Names of the required settings that are empty."""
        values = (self.token, self.app_secret, self.verify_token, self.account_id)
        return [name for name, value in zip(REQUIRED, values) if not value]

    @property
    def ready(self) -> bool:
        """Everything the channel needs is set. A channel that is not ready is simply not mounted."""
        return not self.missing

    @property
    def link_secret(self) -> bytes:
        """The key for link.LinkCodes(secret=...): derived from the app secret, never the secret itself."""
        return hmac.new(self.app_secret.encode(), b"instagram-link-codes", hashlib.sha256).digest()


def _whole(value: str, default: int) -> int:
    """A count from the environment: empty is the default, anything that is not a whole number stops the start."""
    if not value:
        return default
    if not (value.isascii() and value.isdigit()):
        raise ValueError("INSTAGRAM_LINK_FLOOD_PER_HOUR must be a whole number, 0 or more")
    return int(value)
