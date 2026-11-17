"""Settings of the WhatsApp channel, read from the environment once, when the app is made.

Secrets (the access token, the app secret, the verify token, the YCloud API key and webhook secret, the admin
password, the PIN pepper and the pepper of the identity document fingerprint) come only from the environment. They
are never logged and never part of repr(Settings).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from accounts import phones
from agent import speech
from payments.text import fold

ROOT = Path(__file__).resolve().parents[2]

# What /healthz reports as missing when it is empty (names only, never values).
REQUIRED = ("WHATSAPP_TOKEN", "WHATSAPP_APP_SECRET", "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_PHONE_NUMBER_ID")
REQUIRED_YCLOUD = ("YCLOUD_API_KEY", "YCLOUD_WEBHOOK_SECRET", "YCLOUD_FROM")
PROVIDERS = ("meta", "ycloud")
KYC_MODES = ("off", "optional", "required")
SIGNUP_MODES = ("open", "invite", "closed", "demo")
KYC_PEPPER_MIN_BYTES = 16  # what kyc.privacy asks of the fingerprint pepper


def digits(raw: str | None) -> str:
    """'+964 771 234 5678' -> '9647712345678'."""
    return re.sub(r"\D", "", raw or "")


def number(raw: object) -> str:
    """A number however it is written, as one sender and one address: digits, an Iraqi mobile in its canonical form
    ('0771 489 9059' -> '9647714899059'). Empty for what cannot be sent to: a user id, or too few or too many digits."""
    written = fold(str(raw or "")).strip()
    if not re.fullmatch(r"\+?[\d \-]+", written):
        return ""
    plain = phones.canonical(written) or digits(written)
    return plain if 7 <= len(plain) <= 15 and not plain.startswith("0") else ""


@dataclass(frozen=True)
class Settings:
    token: str = field(default="", repr=False)  # WHATSAPP_TOKEN: a system-user access token
    app_secret: str = field(default="", repr=False)  # WHATSAPP_APP_SECRET: checks X-Hub-Signature-256
    verify_token: str = field(default="", repr=False)  # WHATSAPP_VERIFY_TOKEN: the webhook handshake
    phone_number_id: str = ""  # WHATSAPP_PHONE_NUMBER_ID: the number this channel answers for
    graph_version: str = "v25.0"  # GRAPH_API_VERSION
    data_dir: Path = ROOT / "data"  # DATA_DIR: the channel keeps everything under DATA_DIR/whatsapp
    daily_budget_usd: float = 2.0  # DAILY_BUDGET_USD: 0 switches the cap off
    max_msgs_per_user_hour: int = 60  # MAX_MSGS_PER_USER_HOUR
    allowed_numbers: frozenset[str] = frozenset()  # ALLOWED_NUMBERS: empty (or "*") = everyone
    manager: str = ""  # MANAGER_WHATSAPP: optional, gets a line on new testers and on the budget cap
    debounce_seconds: float = 4.0  # DEBOUNCE_SECONDS: quiet time that joins a burst of texts
    # WHATSAPP_SENDER_SALT: keys the hash that names each sender in the channel's own store (empty: the app
    # secret). The accounts are keyed by the number itself; changing the salt only forgets open PIN steps.
    sender_salt: str = field(default="", repr=False)
    accounts_db: Path | None = None  # ACCOUNTS_DB: default DATA_DIR/accounts.db (one database for everyone)
    # SIGNUP_MODE: open | invite (only numbers the admin added) | closed | demo (the first message opens a demo account
    # with pretend money, a short past and a PIN shown once: channels/whatsapp/demo.py). Anything else is refused.
    signup_mode: str = "open"
    signup_credit_iqd: int = 0  # SIGNUP_CREDIT_IQD: a grant credited at sign-up (from the cash account)
    default_daily_limit: int = 1_000_000  # DEFAULT_DAILY_LIMIT_IQD for a new account
    admin_user: str = "karar"  # ADMIN_USER
    admin_password: str = field(default="", repr=False)  # ADMIN_PASSWORD: empty (and no hash) -> /admin is 404
    admin_password_hash: str = field(default="", repr=False)  # ADMIN_PASSWORD_HASH: a PBKDF2 hash instead
    pin_pepper_set: bool = False  # ACCOUNTS_PIN_PEPPER is set (the value itself is read by wallet.db)
    service_number: str = "9647714899059"  # SERVICE_NUMBER: this channel's own number, for wa.me links
    meta_price_usd: float = 0.0  # META_PRICE_USD: per outbound message, for the admin's estimate only
    allow_unsigned: bool = False  # WHATSAPP_ALLOW_UNSIGNED=1: local testing only, never in production
    # VOICE_REPLY_MODE: mirror (a voice note answers a voice note) | always | off. Needs a core that speaks.
    voice_reply_mode: str = "mirror"
    provider: str = "meta"  # WHATSAPP_PROVIDER: meta (the Cloud API directly) | ycloud (through YCloud)
    ycloud_api_key: str = field(default="", repr=False)  # YCLOUD_API_KEY: only ever sent to api.ycloud.com
    ycloud_webhook_secret: str = field(default="", repr=False)  # YCLOUD_WEBHOOK_SECRET: checks YCloud-Signature
    ycloud_from: str = ""  # YCLOUD_FROM: the number this channel sends from and answers for, through YCloud
    # SIGNUP_KYC: off | optional | required. The identity document step of sign-up (kyc/): after the name, before
    # the PIN. optional offers «Later» (the account opens at the small limits of kyc.policy); required has no skip.
    # The step needs a reader (a usable "vision" route in the model layer) and KYC_PEPPER; without either the mode
    # in force is off (kyc_mode below), and the app says so in one log line at start.
    signup_kyc: str = "optional"
    # KYC_PEPPER: keys the fingerprint of a document number (HMAC), so one document on two accounts can be seen
    # without keeping the number. At least 16 bytes. Changing it makes old fingerprints unmatchable.
    kyc_pepper: str = field(default="", repr=False)
    # KYC_ALLOW_SPECIMEN=1: accept documents marked SPECIMEN. Only for a demo with the drawn images in data/kyc/.
    kyc_allow_specimen: bool = False
    # VOICE_APPROVAL=1: voice approval (accounts/voiceprint.py, README "Voice approval"). After a payment asked for in
    # a voice note and confirmed with the PIN, the owner's voice is kept as a speaker vector; a later voice note that
    # matches it approves a payment of at most VOICE_APPROVAL_MAX_IQD to a known payee with the tap and no PIN. Needs
    # the speaker model (VOICEPRINT_MODEL), sherpa-onnx and ffmpeg: without any of them it is off (one log line), and
    # nothing (the demo intro included) mentions it.
    voice_approval: bool = False
    voice_match_threshold: float = 0.70  # VOICE_MATCH_THRESHOLD: cosine similarity a voice note must reach
    voice_approval_max_iqd: int = 100_000  # VOICE_APPROVAL_MAX_IQD: the most one voice-approved payment may be
    voiceprint_model: str = ""  # VOICEPRINT_MODEL: path of the CAM++ speaker model (.onnx)
    # HUMAN_PACE: a short typing pause before each text bubble, scaled to its length (capped at 2 s). On from the
    # environment (HUMAN_PACE=0 switches it off); off for a Settings made in code, so tests run at full speed.
    human_pace: bool = False

    def __post_init__(self) -> None:
        # Refused at start-up: a typo must not leave the channel on the provider the operator meant to leave.
        if self.provider not in PROVIDERS:
            raise ValueError("WHATSAPP_PROVIDER must be meta or ycloud")
        if self.signup_kyc not in KYC_MODES:  # a typo must not switch the identity step off without a word
            raise ValueError("SIGNUP_KYC must be off, optional or required")
        if self.signup_mode not in SIGNUP_MODES:  # a typo used to mean "open" without a word
            raise ValueError("SIGNUP_MODE must be open, invite, closed or demo")
        if not 0.5 <= self.voice_match_threshold < 1.0:  # "7" for 0.7 must not let every voice through
            raise ValueError("VOICE_MATCH_THRESHOLD must be at least 0.5 and below 1")
        if self.voice_approval_max_iqd <= 0:
            raise ValueError("VOICE_APPROVAL_MAX_IQD must be a positive amount")

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        def get(name: str, default: str = "") -> str:
            return (env.get(name) or "").strip() or default

        def num(name: str, default: float) -> float:
            try:
                return float(get(name) or default)
            except ValueError:
                return default

        allowed = frozenset(d for d in (digits(x) for x in get("ALLOWED_NUMBERS").split(",")) if d)
        return cls(
            token=get("WHATSAPP_TOKEN"),
            app_secret=get("WHATSAPP_APP_SECRET"),
            verify_token=get("WHATSAPP_VERIFY_TOKEN"),
            phone_number_id=get("WHATSAPP_PHONE_NUMBER_ID"),
            graph_version=get("GRAPH_API_VERSION", cls.graph_version),
            data_dir=Path(get("DATA_DIR") or cls.data_dir),
            daily_budget_usd=num("DAILY_BUDGET_USD", cls.daily_budget_usd),
            max_msgs_per_user_hour=int(num("MAX_MSGS_PER_USER_HOUR", cls.max_msgs_per_user_hour)),
            allowed_numbers=allowed,
            manager=digits(get("MANAGER_WHATSAPP")),
            debounce_seconds=max(0.0, num("DEBOUNCE_SECONDS", cls.debounce_seconds)),
            sender_salt=get("WHATSAPP_SENDER_SALT"),
            accounts_db=Path(get("ACCOUNTS_DB")) if get("ACCOUNTS_DB") else None,
            signup_mode=get("SIGNUP_MODE", cls.signup_mode).lower(),
            signup_credit_iqd=max(0, int(num("SIGNUP_CREDIT_IQD", 0))),
            default_daily_limit=max(0, int(num("DEFAULT_DAILY_LIMIT_IQD", cls.default_daily_limit))),
            admin_user=get("ADMIN_USER", cls.admin_user),
            admin_password=get("ADMIN_PASSWORD"),
            admin_password_hash=get("ADMIN_PASSWORD_HASH"),
            pin_pepper_set=bool(get("ACCOUNTS_PIN_PEPPER")),
            service_number=digits(get("SERVICE_NUMBER", cls.service_number)),
            meta_price_usd=max(0.0, num("META_PRICE_USD", 0.0)),
            allow_unsigned=get("WHATSAPP_ALLOW_UNSIGNED").lower() in ("1", "true", "yes"),
            voice_reply_mode=speech.reply_mode(env),
            provider=get("WHATSAPP_PROVIDER", cls.provider).lower(),
            ycloud_api_key=get("YCLOUD_API_KEY"),
            ycloud_webhook_secret=get("YCLOUD_WEBHOOK_SECRET"),
            ycloud_from=number(get("YCLOUD_FROM")),
            signup_kyc=get("SIGNUP_KYC", cls.signup_kyc).lower(),
            kyc_pepper=get("KYC_PEPPER"),
            kyc_allow_specimen=get("KYC_ALLOW_SPECIMEN").lower() in ("1", "true", "yes"),
            voice_approval=get("VOICE_APPROVAL").lower() in ("1", "true", "yes", "on"),
            voice_match_threshold=num("VOICE_MATCH_THRESHOLD", cls.voice_match_threshold),
            voice_approval_max_iqd=int(num("VOICE_APPROVAL_MAX_IQD", cls.voice_approval_max_iqd)),
            voiceprint_model=get("VOICEPRINT_MODEL"),
            human_pace=get("HUMAN_PACE", "1").lower() not in ("0", "false", "no", "off"),
        )

    @property
    def missing(self) -> list[str]:
        """Names of the required settings that are empty, for this provider (for /healthz)."""
        if self.is_ycloud:
            values = (self.ycloud_api_key, self.ycloud_webhook_secret, self.ycloud_from)
            return [name for name, value in zip(REQUIRED_YCLOUD, values) if not value]
        values = (self.token, self.app_secret, self.verify_token, self.phone_number_id)
        return [name for name, value in zip(REQUIRED, values) if not value]

    @property
    def is_ycloud(self) -> bool:
        return self.provider == "ycloud"

    @property
    def credential(self) -> str:
        """What this provider's sends are authorised with."""
        return self.ycloud_api_key if self.is_ycloud else self.token

    @property
    def unsigned_allowed(self) -> bool:
        """WHATSAPP_ALLOW_UNSIGNED, for local testing: any webhook secret that is set switches it off. With YCloud that
        counts a Meta secret left behind too, so that one forgotten setting cannot open the webhook."""
        return self.allow_unsigned and not self.app_secret and not (self.is_ycloud and self.ycloud_webhook_secret)

    @property
    def kyc_pepper_bytes(self) -> bytes:
        return self.kyc_pepper.encode("utf-8")

    @property
    def kyc_pepper_ok(self) -> bool:
        return len(self.kyc_pepper_bytes) >= KYC_PEPPER_MIN_BYTES

    def kyc_mode(self, can_read: bool) -> str:
        """The identity step's mode in force: SIGNUP_KYC, or off when no reader can read a photo (`can_read`: a
        usable vision route, or a reader handed to the channel) or the fingerprint pepper is missing or too short."""
        if self.signup_kyc == "off" or not can_read or not self.kyc_pepper_ok:
            return "off"
        return self.signup_kyc

    @property
    def accounts_path(self) -> Path:
        return self.accounts_db or Path(self.data_dir) / "accounts.db"

    @property
    def admin_enabled(self) -> bool:
        return bool(self.admin_password or self.admin_password_hash)

    @property
    def sender_secret(self) -> str:
        """The key of the sender hash: WHATSAPP_SENDER_SALT, else the app secret (so it stays the same across a change
        of provider, and an open PIN step survives it). A YCloud service with neither falls back on its webhook secret,
        rather than on a plain hash of the number."""
        return self.sender_salt or self.app_secret or (self.ycloud_webhook_secret if self.is_ycloud else "")

    def allowed(self, sender: str) -> bool:
        return not self.allowed_numbers or digits(sender) in self.allowed_numbers
