import os
import sys
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from contextlib import asynccontextmanager

sys.path.append(os.path.dirname(__file__))
from auth import (
    verify_password,
    get_admin_user,
    create_session_token,
    verify_session_token,
    init_credentials,
    update_admin_password
)
from engine_manager import GameServerManager
import services
import settings_manager
from webhook_manager import WebhookDispatcher, get_default_templates

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"
PANEL_PORT = int(os.getenv("PANEL_PORT", "28456"))

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-start Valheim game engine on container boot if binary exists
    binary = os.getenv("VALHEIM_BINARY", "/valheim/valheim_server.x86_64")
    if os.path.exists(binary) and os.getenv("AUTO_START_GAME", "true").lower() == "true":
        print("==> Auto-starting Valheim dedicated server engine via supervisor...")
        GameServerManager.get_instance().start()

    # Start automated background backup worker
    services.start_auto_backup_worker()
    yield

    # Graceful flush on container stop
    print("==> Container stopping: flushing Valheim world saves...")
    GameServerManager.get_instance().stop(timeout=30)

app = FastAPI(title="Thorpedo Valheim Panel", lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Initialize admin credentials on boot
init_credentials()

def deep_update(target: dict, source: dict) -> dict:
    for k, v in source.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            deep_update(target[k], v)
        else:
            target[k] = v
    return target

def get_current_user(request: Request) -> Optional[str]:
    if not AUTH_ENABLED:
        return "admin"
    cookie = request.cookies.get("thorpedo_session")
    user = verify_session_token(cookie)
    return user

def require_auth(request: Request):
    if not AUTH_ENABLED:
        return "admin"
    # Allow Bearer token in Authorization header
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_tok = auth_header.split(" ", 1)[1].strip()
        bearer_user = verify_session_token(bearer_tok)
        if bearer_user:
            return bearer_user
        internal_key = os.getenv("PANEL_SECRET_KEY") or os.getenv("SESSION_SECRET")
        if internal_key and bearer_tok == internal_key:
            return "internal"

    # Allow loopback internal calls if identified
    client_ip = request.client.host if request.client else ""
    if client_ip in ("127.0.0.1", "::1", "localhost", "100.96.207.8"):
        if request.headers.get("X-Thorpedo-Internal") == "true":
            return "internal"

    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return user

# --- HEALTHCHECK & READINESS (UNAUTHENTICATED) ---

@app.get("/healthz")
@app.get("/api/health")
async def healthz():
    mgr = GameServerManager.get_instance()
    st = mgr.status
    return {
        "status": "ok",
        "game_status": st,
        "playfab_ready": mgr.playfab_active,
        "join_code_active": bool(mgr.playfab_join_code)
    }

# --- AUTH ROUTES ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/" if get_current_user(request) else "/login", status_code=302)
    return templates.TemplateResponse(request=request, name="login.html", context={"error": None})

@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    admin = get_admin_user()
    if username == admin["username"] and verify_password(password, admin["password_hash"]):
        token = create_session_token(username)
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key="thorpedo_session",
            value=token,
            httponly=True,
            max_age=86400 * 7,
            samesite="lax"
        )
        return response
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Username or password incorrect!"})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("thorpedo_session")
    return response

# --- MAIN DASHBOARD ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = get_current_user(request)
    if not user and AUTH_ENABLED:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request=request, name="index.html", context={"user": user or "admin"})

# --- API ENDPOINTS ---

@app.get("/api/status")
async def get_status(user: str = Depends(require_auth)):
    return services.get_full_status()

class ActionPayload(BaseModel):
    action: str
    delay_seconds: Optional[int] = 0
    delay: Optional[int] = 0

@app.post("/api/actions/execute")
async def execute_action(payload: ActionPayload, user: str = Depends(require_auth)):
    delay = payload.delay_seconds if payload.delay_seconds else (payload.delay or 0)
    res = services.execute_server_action(payload.action, delay=delay)
    return res

@app.get("/api/logs")
async def get_logs(filter: Optional[str] = None, count: Optional[int] = 100, user: str = Depends(require_auth)):
    mgr = GameServerManager.get_instance()
    return mgr.get_logs(filter_type=filter, count=count)

@app.get("/api/modifiers")
async def get_modifiers(user: str = Depends(require_auth)):
    return services.get_modifiers()

class ModifiersPayload(BaseModel):
    modifiers: Dict[str, str]
    restart: bool = False

@app.post("/api/modifiers")
async def save_modifiers(payload: ModifiersPayload, user: str = Depends(require_auth)):
    success = services.save_modifiers(payload.modifiers)
    if not success:
        return JSONResponse(status_code=500, content={"message": "Failed to write modifiers to environment."})
    msg = "World modifiers saved successfully!"
    if payload.restart:
        services.execute_server_action("restart_graceful", delay=0)
        msg += " Game server is restarting to apply changes."
    return {"success": True, "message": msg}

class ServerConfigPayload(BaseModel):
    name: Optional[str] = None
    password: Optional[str] = None
    access_password: Optional[str] = None
    show_access_password: Optional[bool] = None
    auto_backup_enabled: Optional[bool] = None
    auto_backup_interval_hours: Optional[int] = None
    retention_count: Optional[int] = None
    restart: Optional[bool] = False

@app.post("/api/server/config")
async def save_server_config(payload: ServerConfigPayload, user: str = Depends(require_auth)):
    settings = settings_manager.load_settings()
    srv = settings.setdefault("server", {})
    if payload.name is not None:
        srv["name"] = payload.name
    if payload.password is not None:
        srv["password"] = payload.password
    if payload.access_password is not None:
        srv["access_password"] = payload.access_password
    if payload.show_access_password is not None:
        srv["show_access_password"] = payload.show_access_password
    if payload.auto_backup_enabled is not None:
        srv["auto_backup_enabled"] = payload.auto_backup_enabled
    if payload.auto_backup_interval_hours is not None:
        srv["auto_backup_interval_hours"] = payload.auto_backup_interval_hours
    if payload.retention_count is not None:
        srv["retention_count"] = payload.retention_count

    settings_manager.save_settings(settings)
    settings_manager.update_server_env(payload.name, payload.password)

    msg = "Server configuration updated!"
    if payload.restart:
        services.execute_server_action("restart_graceful", delay=0)
        msg += " Server is rebooting to load updated configuration."
    return {"success": True, "message": msg}

@app.get("/api/snapshots")
async def get_snapshots(user: str = Depends(require_auth)):
    return services.get_snapshots()

@app.post("/api/snapshots/create")
async def create_snapshot_api(user: str = Depends(require_auth)):
    res = services.create_snapshot()
    return res

@app.get("/api/snapshots/download/{filename}")
async def download_snapshot(filename: str, user: str = Depends(require_auth)):
    clean_fn = os.path.basename(filename)
    safe_dir = os.path.abspath(services.BACKUPS_DIR)
    file_path = os.path.abspath(os.path.join(safe_dir, clean_fn))
    if not file_path.startswith(safe_dir + os.sep) and file_path != safe_dir:
        raise HTTPException(status_code=400, detail="Invalid snapshot path")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Snapshot archive not found")
    return FileResponse(file_path, filename=clean_fn, media_type="application/zip")

@app.delete("/api/snapshots/{filename}")
async def delete_snapshot_endpoint(filename: str, user: str = Depends(require_auth)):
    success = services.delete_snapshot(filename)
    return {"success": success, "message": f"Snapshot '{filename}' deleted." if success else "Snapshot not found."}

class RollbackPayload(BaseModel):
    id: Optional[str] = None
    snapshot_id: Optional[str] = None
    safety: Optional[bool] = True

@app.post("/api/snapshots/rollback")
async def rollback(payload: RollbackPayload, user: str = Depends(require_auth)):
    target_id = payload.id or payload.snapshot_id or "latest"
    res = services.rollback_snapshot(target_id, safety=payload.safety if payload.safety is not None else True)
    return res

@app.post("/api/snapshots/upload-world")
async def upload_world_api(file: UploadFile = File(...), user: str = Depends(require_auth)):
    contents = await file.read()
    if len(contents) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 500MB)")
    fname = file.filename or "uploaded_world.zip"
    res = services.upload_world_save(fname, contents)
    return res

@app.get("/api/mods")
async def get_mods(user: str = Depends(require_auth)):
    return services.get_installed_mods()

class ToggleModPayload(BaseModel):
    name: str
    enable: bool

@app.post("/api/mods/toggle")
async def toggle_mod(payload: ToggleModPayload, user: str = Depends(require_auth)):
    success = services.toggle_mod(payload.name, payload.enable)
    return {"success": success}

class DeleteModPayload(BaseModel):
    name: str

@app.delete("/api/mods/{name}")
async def delete_mod_endpoint(name: str, user: str = Depends(require_auth)):
    success = services.delete_mod(name)
    return {"success": success, "message": f"Plugin '{name}' deleted." if success else "Plugin not found."}

@app.post("/api/mods/delete")
async def delete_mod_post(payload: DeleteModPayload, user: str = Depends(require_auth)):
    success = services.delete_mod(payload.name)
    return {"success": success, "message": f"Plugin '{payload.name}' deleted." if success else "Plugin not found."}

@app.post("/api/mods/upload")
async def upload_mod_api(file: UploadFile = File(...), user: str = Depends(require_auth)):
    contents = await file.read()
    if len(contents) > 200 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="File too large (max 200MB)")
    fname = file.filename or "uploaded_mod.dll"
    res = services.upload_mod(fname, contents)
    return res

class ModConfigPayload(BaseModel):
    content: str

@app.get("/api/mods/config/{name}")
async def get_mod_config_endpoint(name: str, user: str = Depends(require_auth)):
    return services.get_mod_config(name)

@app.post("/api/mods/config/{name}")
async def save_mod_config_endpoint(name: str, payload: ModConfigPayload, user: str = Depends(require_auth)):
    res = services.save_mod_config(name, payload.content)
    if not res.get("ok"):
        raise HTTPException(status_code=500, detail=res.get("message", "Failed to save mod config"))
    return res

@app.get("/api/players")
async def get_players_data(user: str = Depends(require_auth)):
    mgr = GameServerManager.get_instance()
    st = mgr.get_status()
    return {
        "active_players": st.get("players", []),
        "admins": services.get_admin_list(),
        "bans": services.get_banned_list()
    }

class PlayerPayload(BaseModel):
    steam_id: str

@app.post("/api/players/admin")
async def add_admin(payload: PlayerPayload, user: str = Depends(require_auth)):
    success = services.add_admin(payload.steam_id)
    return {"success": success}

@app.delete("/api/players/admin")
async def remove_admin(payload: PlayerPayload, user: str = Depends(require_auth)):
    success = services.remove_admin(payload.steam_id)
    return {"success": success}

@app.post("/api/players/kick")
async def kick_player(payload: PlayerPayload, user: str = Depends(require_auth)):
    # payload can be steam_id or character name
    target = payload.steam_id.strip()
    mgr = GameServerManager.get_instance()
    # Send engine command
    mgr.send_command(f"kick {target}")
    # Force disconnect cleanup in manager session tracker
    mgr._handle_player_disconnect(target)
    return {"ok": True, "message": f"Player '{target}' kicked from realm"}

@app.post("/api/players/ban")
async def add_banned(payload: PlayerPayload, user: str = Depends(require_auth)):
    success = services.add_banned(payload.steam_id)
    return {"success": success}

@app.delete("/api/players/ban")
async def remove_banned(payload: PlayerPayload, user: str = Depends(require_auth)):
    success = services.remove_banned(payload.steam_id)
    return {"success": success}

class CommandPayload(BaseModel):
    command: str

@app.post("/api/console/command")
async def send_command(payload: CommandPayload, user: str = Depends(require_auth)):
    mgr = GameServerManager.get_instance()
    success = mgr.send_command(payload.command)
    return {"success": success, "message": f"Command '{payload.command}' delivered to engine." if success else "Failed: engine is not running."}

# --- SETTINGS ENDPOINTS ---

@app.get("/api/settings")
async def get_settings_api(user: str = Depends(require_auth)):
    return settings_manager.get_sanitized_settings()

@app.post("/api/settings")
async def update_settings_api(payload: Dict[str, Any], user: str = Depends(require_auth)):
    existing = settings_manager.load_settings()
    
    # Check github token mask
    new_gh = payload.get("github", {})
    if new_gh.get("token") and ("..." in new_gh["token"] or new_gh["token"] == "****"):
        new_gh["token"] = existing.get("github", {}).get("token", "")
        
    # Check discord webhook URL mask
    new_disc = payload.get("discord", {})
    if new_disc.get("webhook_url") and "..." in new_disc["webhook_url"]:
        new_disc["webhook_url"] = existing.get("discord", {}).get("webhook_url", "")

    # Check access password mask
    new_srv = payload.get("server", {})
    if new_srv.get("access_password") == "••••••••":
        new_srv["access_password"] = existing.get("server", {}).get("access_password", "")

    deep_update(existing, payload)
    success = settings_manager.save_settings(existing)
    
    if payload.get("server"):
        srv = payload["server"]
        settings_manager.update_server_env(srv.get("name"), srv.get("password"))

    return {"ok": success, "message": "Settings updated successfully."}

class DiscordTestPayload(BaseModel):
    url: Optional[str] = ""
    mention_role: Optional[str] = ""
    embed_color: Optional[str] = "#D97706"

@app.post("/api/settings/discord/test")
async def test_discord_api(payload: DiscordTestPayload, user: str = Depends(require_auth)):
    url = payload.url or ""
    settings = settings_manager.load_settings()
    if not url or "..." in url:
        url = settings.get("discord", {}).get("webhook_url", "")
        if not url:
            for wh in settings.get("discord_webhooks", []):
                if wh.get("url"):
                    url = wh["url"]
                    break

    res = settings_manager.test_discord_webhook(url, payload.mention_role or "", payload.embed_color or "#D97706")
    return res

class GitHubTestPayload(BaseModel):
    repo: str
    token: str

@app.post("/api/settings/github/test")
async def test_github_api(payload: GitHubTestPayload, user: str = Depends(require_auth)):
    tok = payload.token
    if "..." in tok or not tok or tok == "****":
        settings = settings_manager.load_settings()
        tok = settings.get("github", {}).get("token", "")
    res = settings_manager.test_github_token(payload.repo, tok)
    return res

class PasswordChangePayload(BaseModel):
    current_password: str
    new_password: str

@app.post("/api/settings/password")
async def change_password_api(payload: PasswordChangePayload, user: str = Depends(require_auth)):
    admin = get_admin_user()
    if not verify_password(payload.current_password, admin["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password incorrect.")
    if len(payload.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters.")
    update_admin_password(payload.new_password)
    return {"ok": True, "message": "Admin password changed successfully."}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=PANEL_PORT, reload=False)
