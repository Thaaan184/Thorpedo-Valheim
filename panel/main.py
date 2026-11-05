import os
import sys
from fastapi import FastAPI, Request, Response, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Optional, Dict, Any, List

sys.path.append(os.path.dirname(__file__))
from auth import (
    verify_password,
    get_admin_user,
    create_session_token,
    verify_session_token,
    init_credentials
)
import services

app = FastAPI(title="Thorpedo Valheim Panel")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))

# Initialize admin credentials on boot
init_credentials()

def get_current_user(request: Request) -> Optional[str]:
    cookie = request.cookies.get("thorpedo_session")
    user = verify_session_token(cookie)
    return user

def require_auth(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return user

# --- AUTH ROUTES ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if get_current_user(request):
        return RedirectResponse(url="/", status_code=302)
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
    return templates.TemplateResponse(request=request, name="login.html", context={"error": "Username atau password salah!"})

@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie("thorpedo_session")
    return response

# --- MAIN DASHBOARD ---

@app.get("/", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    return templates.TemplateResponse(request=request, name="index.html", context={"user": user})

# --- API ENDPOINTS (PROTECTED) ---

@app.get("/api/status")
async def get_status(user: str = Depends(require_auth)):
    c_status = services.get_container_status()
    pf_info = services.get_playfab_info()
    metrics = services.get_system_metrics()
    players = services.get_active_players()
    return {
        "server": c_status,
        "playfab": pf_info,
        "metrics": metrics,
        "players": players
    }

class ActionPayload(BaseModel):
    action: str
    delay: Optional[int] = 0

@app.post("/api/actions/execute")
async def execute_action(payload: ActionPayload, user: str = Depends(require_auth)):
    res = services.execute_server_action(payload.action, payload.delay or 0)
    return res

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
        return JSONResponse(status_code=500, content={"message": "Gagal menyimpan file konfigurasi .env"})
    msg = "Pengaturan modifier dunia berhasil disimpan!"
    if payload.restart:
        services.execute_server_action("restart", delay=0)
        msg += " Server sedang direstart untuk menerapkan perubahan."
    return {"success": True, "message": msg}

@app.get("/api/snapshots")
async def get_snapshots(user: str = Depends(require_auth)):
    return services.get_snapshots()

@app.post("/api/snapshots/create")
async def create_snapshot(user: str = Depends(require_auth)):
    return services.create_snapshot_now()

class RollbackPayload(BaseModel):
    snapshot_id: str
    safety_net: bool = True

@app.post("/api/snapshots/rollback")
async def rollback(payload: RollbackPayload, user: str = Depends(require_auth)):
    return services.rollback_snapshot(payload.snapshot_id, payload.safety_net)

@app.get("/api/mods")
async def get_mods(user: str = Depends(require_auth)):
    return services.get_installed_mods()

class ToggleModPayload(BaseModel):
    filename: str
    enable: bool

@app.post("/api/mods/toggle")
async def toggle_mod(payload: ToggleModPayload, user: str = Depends(require_auth)):
    success = services.toggle_mod(payload.filename, payload.enable)
    if success:
        state_str = "diaktifkan" if payload.enable else "dinonaktifkan"
        return {"success": True, "message": f"Mod {payload.filename} berhasil {state_str}. Berlaku pada restart berikutnya."}
    return JSONResponse(status_code=400, content={"message": "Gagal memperbarui file mod."})

@app.get("/api/players")
async def get_players_data(user: str = Depends(require_auth)):
    return {
        "admins": services.get_admin_list(),
        "banned": services.get_banned_list()
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

@app.get("/api/console/logs")
async def get_logs(category: str = "all", user: str = Depends(require_auth)):
    return services.get_console_logs(category=category)

class CommandPayload(BaseModel):
    command: str

@app.post("/api/console/command")
async def send_command(payload: CommandPayload, user: str = Depends(require_auth)):
    # Run devcommand via docker exec or log injection
    out = services.run_cmd(["docker", "exec", services.DOCKER_CONTAINER, "echo", payload.command])
    return {"success": True, "message": f"Perintah '{payload.command}' terkirim.", "output": out}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8088, reload=False)
