import os
import sys
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

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

AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"
PANEL_PORT = int(os.getenv("PANEL_PORT", "28456"))

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-start Valheim game engine on container boot if binary exists
    binary = os.getenv("VALHEIM_BINARY", "/valheim/valheim_server.x86_64")
    if os.path.exists(binary) and os.getenv("AUTO_START_GAME", "true").lower() == "true":
        print("==> Auto-starting Valheim dedicated server engine via supervisor...")
        GameServerManager.get_instance().start()
    yield
    # Graceful flush on container stop
    print("==> Container stopping: flushing Valheim world saves...")
    GameServerManager.get_instance().stop(timeout=30)

app = FastAPI(title="Thorpedo Valheim Panel", lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Initialize admin credentials on boot
init_credentials()

def get_current_user(request: Request) -> Optional[str]:
    if not AUTH_ENABLED:
        return "admin"
    cookie = request.cookies.get("thorpedo_session")
    user = verify_session_token(cookie)
    return user

def require_auth(request: Request):
    if not AUTH_ENABLED:
        return "admin"
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return user

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
    file_path = os.path.join(services.BACKUPS_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Snapshot archive not found")
    return FileResponse(file_path, filename=filename, media_type="application/zip")

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

@app.post("/api/mods/upload")
async def upload_mod_api(file: UploadFile = File(...), user: str = Depends(require_auth)):
    contents = await file.read()
    fname = file.filename or "uploaded_mod.dll"
    res = services.upload_mod(fname, contents)
    return res

@app.get("/api/players")
async def get_players_data(user: str = Depends(require_auth)):
    return {
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
    return {"success": success, "message": f"Command '{payload.command}' delivered to engine."}

# --- SETTINGS ENDPOINTS ---

@app.get("/api/settings")
async def get_settings_api(user: str = Depends(require_auth)):
    return settings_manager.get_sanitized_settings()

@app.post("/api/settings")
async def update_settings_api(payload: Dict[str, Any], user: str = Depends(require_auth)):
    existing = settings_manager.load_settings()
    
    # Check github token mask
    new_gh = payload.get("github", {})
    if new_gh.get("token") and "..." in new_gh["token"]:
        new_gh["token"] = existing.get("github", {}).get("token", "")
        
    # Check discord webhooks
    new_whs = payload.get("discord_webhooks", [])
    exist_wh_map = {w.get("id"): w for w in existing.get("discord_webhooks", [])}
    for wh in new_whs:
        if wh.get("url") and "..." in wh["url"]:
            old_w = exist_wh_map.get(wh.get("id"))
            if old_w:
                wh["url"] = old_w.get("url", "")

    existing.update(payload)
    success = settings_manager.save_settings(existing)
    
    if payload.get("server"):
        srv = payload["server"]
        settings_manager.update_server_env(srv.get("name"), srv.get("password"))

    return {"ok": success, "message": "Settings updated successfully."}

class DiscordTestPayload(BaseModel):
    url: str
    mention_role: Optional[str] = ""
    embed_color: Optional[str] = "#D97706"

@app.post("/api/settings/discord/test")
async def test_discord_api(payload: DiscordTestPayload, user: str = Depends(require_auth)):
    url = payload.url
    if "..." in url:
        settings = settings_manager.load_settings()
        for wh in settings.get("discord_webhooks", []):
            if wh.get("url") and wh["url"].startswith(url[:15]):
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
    if "..." in tok or not tok:
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
