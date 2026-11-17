"""Chat app for the bill-pay agent: a mobile-first web page over the orchestrator and a mock wallet.

Run:   uvicorn web.app:app --port 8080        then open http://127.0.0.1:8080
Core:  BILLPAY_CORE="package.module:Class"   (default: core.stub.StubCore, offline, no keys)

Each browser sends its own session id (header X-Demo-Session). The server keeps one demo per id:
an in-memory synthetic wallet, one orchestrator Session and the picked user. Money only moves
through POST /api/button with the card's quote id and the PIN, exactly as in agent/demo.py. A tapped chip
(POST /api/option) only answers a question or opens a menu row: the server looks its id up, and it never pays.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent.wallet_client import WalletError, WalletUnavailable
from web.service import DEMO_FAULTS, PIN_LENGTH, VOICE_NOTES, Demo, DemoStore, voice_of

STATIC = Path(__file__).resolve().parent / "static"
MAX_TEXT = 500
MAX_AUDIO_BYTES = 2 * 1024 * 1024  # about two minutes of a compressed voice note


class StartIn(BaseModel):
    user_id: str


class TextIn(BaseModel):
    text: str = Field(max_length=MAX_TEXT)


class ButtonIn(BaseModel):
    quote_id: str = Field(max_length=64)
    button: Literal["confirm", "change", "cancel"]
    pin: str | None = Field(default=None, max_length=12)


class OptionIn(BaseModel):
    option_id: str = Field(min_length=1, max_length=64)


class FaultIn(BaseModel):
    mode: Literal["none", "drop_after_commit", "biller_down", "network_down"]


SessionId = Annotated[str, Header(alias="X-Demo-Session")]


def create_app(store: DemoStore | None = None) -> FastAPI:
    """Build the chat app. Tests pass their own DemoStore (for example with a small seed)."""
    demos = store or DemoStore()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        demos.close()

    app = FastAPI(title="Bill-pay chat", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.demos = demos

    def demo_for(sid: str) -> Demo:
        try:
            return demos.get(sid)
        except ValueError as exc:
            raise HTTPException(400, {"error": "bad_session"}) from exc

    @app.exception_handler(WalletUnavailable)
    async def _wallet_down(_: Request, exc: WalletUnavailable) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": {"error": "wallet_unavailable"}})

    # ------------------------------------------------------------ pages

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    # ------------------------------------------------------------ API

    @app.get("/api/config")
    def config() -> dict:
        return {"voice": demos.voice, "pin_length": PIN_LENGTH, "faults": list(DEMO_FAULTS),
                "speaks": demos.speaks, "voice_reply_mode": demos.voice_mode}

    @app.get("/api/users")
    def users() -> list[dict]:
        return [{k: v for k, v in u.items() if k != "demo_pin"} for u in demos.users]

    @app.post("/api/start")
    def start(body: StartIn, sid: SessionId) -> dict:
        if body.user_id not in demos.user_ids:
            raise HTTPException(404, {"error": "unknown_user"})
        demo = demo_for(sid)
        with demo.lock:
            demo.start(body.user_id)
            return {
                "profile": demo.profile(),
                "suggestions": demo.suggestions(),
                "history": demo.history(),
                "demo_pin": demos.demo_pin(body.user_id),
                "menu": demo.menu(),
            }

    @app.post("/api/message")
    def message(body: TextIn, sid: SessionId) -> dict:
        text = body.text.strip()
        if not text:
            raise HTTPException(400, {"error": "empty"})
        demo = demo_for(sid)
        with demo.lock:
            return demo.text(text)

    @app.post("/api/button")
    def button(body: ButtonIn, sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:  # one tap at a time per browser; the wallet itself never pays a quote twice
            return demo.button(body.quote_id, body.button, body.pin)

    @app.post("/api/option")
    def option(body: OptionIn, sid: SessionId) -> dict:
        """A tapped chip. Its id is resolved on the server against the open question; it cannot pay."""
        demo = demo_for(sid)
        with demo.lock:
            return demo.option(body.option_id)

    @app.post("/api/explain")
    def explain(sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:
            return demo.explain()

    @app.post("/api/voice")
    async def voice(request: Request, sid: SessionId) -> JSONResponse:
        audio = await request.body()
        if len(audio) > MAX_AUDIO_BYTES:
            raise HTTPException(413, {"error": "too_long"})
        demo = demo_for(sid)
        transcribe = voice_of(demo.core)
        if transcribe is None:
            gender = (demo.profile() or {}).get("gender", "f")
            return JSONResponse({"voice": False, "note": VOICE_NOTES.get(demo.lang(), VOICE_NOTES["iq"])[gender]})
        if not audio:
            raise HTTPException(400, {"error": "empty"})
        mime = (request.headers.get("content-type") or "audio/webm").split(";")[0].strip()[:64]
        with demo.lock:
            payload = demo.voice(audio, mime)
        return JSONResponse({"voice": True, **payload})

    @app.post("/api/check")
    def check(sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:
            return demo.check()

    @app.get("/api/history")
    def history(sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:
            return {"profile": demo.profile(), "items": demo.history()}

    @app.post("/api/reset")
    def reset(sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:
            demo.reset()
            demo.start(demo.user_id)
            return {
                "profile": demo.profile(),
                "suggestions": demo.suggestions(),
                "history": demo.history(),
                "demo_pin": demos.demo_pin(demo.user_id),
                "menu": demo.menu(),
            }

    @app.post("/api/demo-fault")
    def demo_fault(body: FaultIn, sid: SessionId) -> dict:
        demo = demo_for(sid)
        with demo.lock:
            try:
                return demo.set_fault(body.mode)
            except WalletError as exc:
                raise HTTPException(400, {"error": exc.code}) from exc

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
