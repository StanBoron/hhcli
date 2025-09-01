"""
FastAPI adapter for hhcli: exposes REST endpoints that wrap existing CLI functions.

Run locally:
  uvicorn hhcli.server:app --reload --port 5179

Assumptions:
- Tokens/config persisted in ~/.hhcli/ via your existing utils/auth code.
- Typer command functions in hhcli/cli.py are *pure* enough to call directly.
- For file-based params (e.g., ids_file, message_file) we provide JSON-based alternatives.

If some CLI functions rely on Typer context, consider moving core logic into
importable helpers (e.g., hhcli/service.py) and call them here.
"""

from __future__ import annotations

import contextlib
import inspect
import io
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Import your existing modules
from . import auth
from . import cli as cli_mod
from . import config as cfg

app = FastAPI(title="hhcli API", version="0.1.0")

# --- CORS for local dev (Vite runs on 5173 by default) ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =========================
# Models
# =========================
class ConfigureIn(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str
    user_agent: str


class OAuthExchangeIn(BaseModel):
    code: str


class SearchIn(BaseModel):
    text: str | None = None
    area: str | None = None
    per_page: int = 20
    page: int = 0


class RespondIn(BaseModel):
    vacancy_id: str
    resume_id: str
    message: str | None = None


class RespondMassIn(BaseModel):
    ids: list[str]
    resume_id: str
    message: str | None = None
    skip_tested: bool = True
    require_letter: bool = False
    rate_limit: float = 0.5
    limit: int | None = None
    dry_run: bool = False


class ResponsesDeleteOut(BaseModel):
    processed: int
    deleted: int
    dry_run: bool
    details: list[dict[str, Any]] = []


# =========================
# Helper glue
# =========================
def _ok(data: Any = None) -> dict[str, Any]:
    return {"ok": True, "data": data}


# =========================
# Config & OAuth
# =========================
@app.post("/api/config")
def set_config(payload: ConfigureIn):
    try:
        cfg_dict = cfg.load_config()
        cfg_dict.update(
            {
                "client_id": payload.client_id,
                "client_secret": payload.client_secret,
                "redirect_uri": payload.redirect_uri,
                "user_agent": payload.user_agent,
            }
        )
        cfg.save_config(cfg_dict)
        return _ok()
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/oauth/url")
def oauth_url():
    try:
        if hasattr(auth, "build_oauth_url"):
            url = auth.build_oauth_url()
            if not isinstance(url, str) or not url.startswith("http"):
                raise ValueError("build_oauth_url returned invalid value")
            return {"url": url}
        # Fallback: CLI
        cli_fn = getattr(cli_mod, "oauth_url", None)
        if callable(cli_fn):
            if "return_url" in inspect.signature(cli_fn).parameters:
                url = cli_fn(return_url=True)
                if isinstance(url, str) and url.startswith("http"):
                    return {"url": url}
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                cli_fn()
            text = buf.getvalue().strip()
            url = text.splitlines()[-1] if text else ""
            if isinstance(url, str) and url.startswith("http"):
                return {"url": url}
        raise HTTPException(500, "Cannot build OAuth URL")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"oauth_url error: {e}") from e


@app.post("/api/oauth/exchange")
def oauth_exchange(payload: OAuthExchangeIn):
    try:
        # Prefer CLI wrapper if present
        cli_fn = getattr(cli_mod, "oauth_exchange", None)
        if callable(cli_fn):
            with contextlib.suppress(TypeError):
                cli_fn(code=payload.code)
            return _ok({"stored": True, "source": "cli"})
        # Low-level via auth
        if hasattr(auth, "exchange_code"):
            tokens = auth.exchange_code(payload.code)
            if not isinstance(tokens, dict):
                raise ValueError("exchange_code returned non-dict")
            access = tokens.get("access_token", "")
            refresh = tokens.get("refresh_token")
            expires_in = tokens.get("expires_in")
            if hasattr(auth, "set_tokens"):
                auth.set_tokens(access_token=access, refresh_token=refresh, expires_in=expires_in)
            else:
                c = cfg.load_config()
                c["access_token"] = access
                if refresh is not None:
                    c["refresh_token"] = refresh
                if expires_in:
                    import time

                    c["token_expires_at"] = int(time.time()) + int(expires_in)
                cfg.save_config(c)
            return _ok({"stored": True, "source": "auth"})
        raise HTTPException(500, "No OAuth exchange implementation found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"oauth_exchange error: {e}") from e


@app.get("/api/oauth/export")
def oauth_export(fmt: str = Query("nested", enum=["nested", "flat"])):
    try:
        c = cfg.load_config()
        data = {
            "access_token": c.get("access_token", ""),
            "refresh_token": c.get("refresh_token", ""),
            "token_expires_at": c.get("token_expires_at"),
            "client_id": c.get("client_id", ""),
            "client_secret": c.get("client_secret", ""),
            "redirect_uri": c.get("redirect_uri", ""),
            "user_agent": c.get("user_agent", ""),
        }
        return _ok(data)
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/oauth/import")
def oauth_import(payload: dict[str, Any]):
    try:
        c = cfg.load_config()
        c.update(
            {
                k: v
                for k, v in payload.items()
                if k
                in {
                    "access_token",
                    "refresh_token",
                    "token_expires_at",
                    "client_id",
                    "client_secret",
                    "redirect_uri",
                    "user_agent",
                }
            }
        )
        cfg.save_config(c)
        return _ok({"imported": True})
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Dictionaries
# =========================
@app.get("/api/dicts/{name}")
def get_dict(name: str):
    try:
        fn_name = f"dicts_{name}"
        fn = getattr(cli_mod, fn_name, None)
        if not fn:
            raise HTTPException(404, f"Dictionary '{name}' not found")
        return _ok(fn())
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Search & Respond
# =========================
@app.post("/api/search")
def search(payload: SearchIn):
    try:
        return _ok(cli_mod.cmd_search(**payload.dict()))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/can-respond")
def can_respond(vacancy_id: str, resume_id: str):
    try:
        return _ok(cli_mod.cmd_can_respond(vacancy_id=vacancy_id, resume_id=resume_id))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/respond")
def respond(payload: RespondIn):
    try:
        return _ok(cli_mod.cmd_respond(**payload.dict()))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/respond/mass")
def respond_mass(payload: RespondMassIn):
    try:
        if hasattr(cli_mod, "respond_mass_adapter"):
            return _ok(cli_mod.respond_mass_adapter(**payload.dict()))
        else:
            from tempfile import NamedTemporaryFile

            with NamedTemporaryFile("w+", suffix=".txt", delete=False, encoding="utf-8") as f:
                f.write("\n".join(payload.ids))
                f.flush()
                return _ok(
                    cli_mod.respond_mass(
                        ids_file=f.name,
                        resume_id=payload.resume_id,
                        message=payload.message,
                        message_file=None,
                        skip_tested=payload.skip_tested,
                        require_letter=payload.require_letter,
                        rate_limit=payload.rate_limit,
                        limit=payload.limit,
                        dry_run=payload.dry_run,
                        out=None,
                    )
                )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Responses maintenance
# =========================
@app.delete("/api/responses", response_model=ResponsesDeleteOut)
def responses_delete(days: int = 21, limit: int = 200, dry_run: bool = True):
    try:
        result = cli_mod.responses_delete(days=days, limit=limit, dry_run=dry_run)
        if isinstance(result, dict):
            return result
        return {"processed": 0, "deleted": 0, "dry_run": dry_run, "details": []}
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Negotiations
# =========================
@app.get("/api/negotiations/ignored")
def negotiations_show_ignored(as_json: bool = True, limit: int = 200, show_path: bool = False):
    try:
        return _ok(
            cli_mod.negotiations_show_ignored(as_json=as_json, limit=limit, show_path=show_path)
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


class UnignoreIn(BaseModel):
    ids: list[str] | None = None
    all_: bool = False


@app.post("/api/negotiations/unignore")
def negotiations_unignore(payload: UnignoreIn):
    try:
        return _ok(cli_mod.negotiations_unignore(ids=payload.ids, all_=payload.all_))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/negotiations/clean-refused")
def negotiations_clean_refused(limit: int = 200, dry_run: bool = True):
    try:
        return _ok(cli_mod.negotiations_clean_refused(limit=limit, dry_run=dry_run))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/negotiations/leave-refused")
def negotiations_leave_refused(limit: int = 200, dry_run: bool = True):
    try:
        return _ok(cli_mod.negotiations_leave_refused(limit=limit, dry_run=dry_run))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Resumes
# =========================
@app.get("/api/resumes")
def my_resumes():
    try:
        return _ok(cli_mod.cmd_my_resumes())
    except Exception as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/resumes/autoraise")
def resume_autoraise(resume_id: str, interval_hours: int = 4, loop: bool = False):
    try:
        return _ok(
            cli_mod.resume_autoraise(resume_id=resume_id, interval_hours=interval_hours, loop=loop)
        )
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# =========================
# Export
# =========================
class ExportIn(BaseModel):
    kind: str
    params: dict[str, Any] = {}


@app.post("/api/export")
def cmd_export(payload: ExportIn):
    try:
        return _ok(cli_mod.cmd_export(kind=payload.kind, **payload.params))
    except Exception as e:
        raise HTTPException(400, str(e)) from e


# Healthcheck
@app.get("/api/health")
def health():
    return {"ok": True}
