"""Two small commands for whoever sets the models up.

    python -m llm check          for each role: its chain, and whether each route is usable. Offline.
    python -m llm ping <role>    one tiny real call through the role's chain. This is the only code in
                                 the repository that reaches a model without being asked by a user.

Neither prints a key. `check` prints the NAMES of the key variables and whether each one is set.
"""

from __future__ import annotations

import argparse
import io
import os
import struct
import sys
import wave
import zlib
from typing import Any, Mapping, Sequence, TextIO

from llm.client import LLM
from llm.errors import LLMError
from llm.registry import ROLES, Registry


def _png(size: int = 32) -> bytes:
    """A plain white square, built here so that no file is needed."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    row = b"\x00" + b"\xff" * (size * 3)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(row * size)) + chunk(b"IEND", b""))


def _wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    """Half a second of silence."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * int(seconds * rate))
    return buffer.getvalue()


def check(registry: Registry, env: Mapping[str, str], out: TextIO) -> int:
    roles = list(ROLES) + sorted(role for role in registry.roles if role not in ROLES)
    ready = 0
    for role in roles:
        entries = registry.status(role, env)
        usable = any(entry["usable"] for entry in entries)
        ready += usable
        print(f"{role}: {'ready' if usable else 'no usable route' if entries else 'not configured'}", file=out)
        for number, entry in enumerate(entries, start=1):
            place = "primary" if number == 1 else f"backup {number - 1}"
            state = "usable" if entry["usable"] else "NOT usable"
            line = f"  {number}. {entry['provider']}:{entry['model']}  [{place}]  {state}: {entry['reason']}"
            print(line, file=out)
            if entry["host"]:
                kind = "local" if entry["local"] else "hosted"
                print(f"       {kind}, {entry['host']}, json mode {entry['json_mode']}", file=out)
            for key in entry["keys"]:
                print(f"       key {key['number']}: ${key['env']} {'set' if key['set'] else 'missing'}", file=out)
    print(f"{ready} of {len(roles)} role(s) ready.", file=out)
    return 0


def ping(llm: LLM, role: str, out: TextIO, *, timeout_s: float, voice: str) -> int:
    role = role.strip().lower()
    if not llm.has(role):
        print(f"{role}: no usable route (run: python -m llm check)", file=out)
        return 1
    yes_no = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"],
              "additionalProperties": False}
    answered: Any
    try:
        if role == "decide":
            answered = llm.choose(role, "hello", ["greeting", "payment"], timeout_s=timeout_s)
        elif role == "vision":
            answered = llm.vision_json(role, _png(), "image/png",
                                       'Is this picture a single plain colour? Answer {"ok": true} or {"ok": false}.',
                                       yes_no, timeout_s=timeout_s)
        elif role == "hear":
            answered = llm.hear_json(role, _wav(), "wav",
                                     [{"role": "user", "content": 'Did you receive audio? Answer {"ok": true}.'}],
                                     yes_no, timeout_s=timeout_s)
        elif role == "stt":
            llm.transcribe(role, _wav(), "wav", timeout_s=timeout_s)
            answered = True   # silence gives an empty text, and that is an answer
        elif role == "tts":
            answered = llm.speech(role, "hello", voice=voice, timeout_s=timeout_s)
        elif role == "understand":
            answered = llm.json(role, [{"role": "user", "content": 'Answer {"ok": true}.'}], yes_no,
                                timeout_s=timeout_s, max_tokens=200)
        else:
            answered = llm.text(role, [{"role": "user", "content": "Reply with the one word: ok"}],
                                timeout_s=timeout_s, max_tokens=200)
    except LLMError as error:
        print(str(error), file=out)
        answered = None

    for number, row in enumerate(llm.stats().get(role, []), start=1):
        if not row["calls"] and not row["skipped"]:
            continue
        result = "skipped" if not row["calls"] else "failed" if row["failures"] else "answered"
        print(f"  {number}. {row['provider']}:{row['model']}  {result}  {row['seconds']:.2f} s  "
              f"{row['tokens_in']} tokens in, {row['tokens_out']} out  ${row['usd']:.6f}", file=out)
    if answered is None:
        print(f"{role}: no route answered", file=out)
        return 1
    print(f"{role}: ok", file=out)
    return 0


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
         transport: Any = None, out: TextIO | None = None) -> int:
    out = sys.stdout if out is None else out
    parser = argparse.ArgumentParser(prog="python -m llm", description="Check the model configuration.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="list every role's chain and whether each route is usable (offline)")
    one = commands.add_parser("ping", help="make one tiny real call through a role's chain")
    one.add_argument("role")
    one.add_argument("--timeout", type=float, default=30.0, help="seconds for the whole call (default 30)")
    one.add_argument("--voice", default="default", help="voice name for the tts role")
    args = parser.parse_args(argv)

    try:
        registry = Registry.from_env(env)
    except LLMError as error:
        print(f"configuration error: {error}", file=out)
        return 2
    if args.command == "check":
        return check(registry, os.environ if env is None else env, out)
    llm = LLM(registry, transport=transport, env=env)
    try:
        return ping(llm, args.role, out, timeout_s=args.timeout, voice=args.voice)
    finally:
        llm.close()


if __name__ == "__main__":
    raise SystemExit(main())
