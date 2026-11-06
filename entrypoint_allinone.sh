#!/usr/bin/env bash
set -e

echo "=== THORPEDO VALHEIM DEDICATED SERVER + CONTROL PANEL ==="
echo "=== Single-Container High-Performance Architecture    ==="

export SERVER_NAME="${SERVER_NAME:-Thorpedo}"
export WORLD_NAME="${WORLD_NAME:-BarudakValheim}"
export SERVER_PORT="${SERVER_PORT:-2456}"
export SERVER_PASSWORD="${SERVER_PASSWORD:-${SERVER_PASS:-z315pn}}"
export SERVER_PASS="$SERVER_PASSWORD"
export PANEL_PORT="${PANEL_PORT:-28456}"
export SAVE_DIR="${SAVE_DIR:-/config}"
export INSTALL_DIR="${INSTALL_DIR:-/valheim}"
export BEPINEX="${BEPINEX:-true}"
export CROSSPLAY="${CROSSPLAY:-true}"
export SAVE_INTERVAL="${SAVE_INTERVAL:-1800}"
export WORLD_MODIFIERS="${WORLD_MODIFIERS:-resources=muchmore portals=casual}"
export WORLD_KEYS="${WORLD_KEYS:-playerevents}"
export UPDATE_ON_START="${UPDATE_ON_START:-false}"

mkdir -p "$SAVE_DIR" "$SAVE_DIR/worlds_local" "$SAVE_DIR/backups" "$INSTALL_DIR"

# 1. STEAMCMD INSTALLATION OR CONTROLLED UPDATE
if [[ ! -f "$INSTALL_DIR/valheim_server.x86_64" || "$UPDATE_ON_START" == "true" ]]; then
  echo "==> SteamCMD check: Installing/verifying Valheim Dedicated Server..."
  STEAMCMD_BIN="$(which steamcmd 2>/dev/null || echo "/steamcmd/steamcmd.sh")"
  "$STEAMCMD_BIN" +@sSteamCmdForcePlatformType linux +force_install_dir "$INSTALL_DIR" +login anonymous +app_update 896660 validate +quit || true
else
  echo "==> Fast Boot: Valheim Dedicated Server binary exists. Skipping SteamCMD check (set UPDATE_ON_START=true to force update)."
fi

# Ensure Steam 64-bit linux binaries are accessible
mkdir -p "$INSTALL_DIR/linux64" /home/valheim/.steam/sdk64 /root/.steam/sdk64
STEAM64_SRC="$(find /root /usr /valheim -name "steamclient.so" 2>/dev/null | grep -E "linux64/steamclient.so" | head -n 1)"
if [[ -z "$STEAM64_SRC" || ! -f "$STEAM64_SRC" ]]; then
  STEAM64_SRC="/root/.local/share/Steam/steamcmd/linux64/steamclient.so"
fi
if [[ -f "$STEAM64_SRC" ]]; then
  echo "==> Deploying 64-bit steamclient.so from $STEAM64_SRC..."
  cp -f "$STEAM64_SRC" "$INSTALL_DIR/linux64/steamclient.so"
  cp -f "$STEAM64_SRC" /home/valheim/.steam/sdk64/steamclient.so
  cp -f "$STEAM64_SRC" /root/.steam/sdk64/steamclient.so 2>/dev/null || true
fi

# 2. BEPINEX MODDING RUNTIME & PERSISTENT PLUGINS
if [[ "$BEPINEX" == "true" && -d /opt/bepinex ]]; then
  echo "==> Synchronizing BepInEx loader and core runtime into $INSTALL_DIR..."
  mkdir -p "$INSTALL_DIR/BepInEx/core" "$INSTALL_DIR/BepInEx/config" "$INSTALL_DIR/BepInEx/plugins"
  
  # Copy doorstop and core binaries
  cp -a /opt/bepinex/doorstop_libs "$INSTALL_DIR/" 2>/dev/null || true
  cp -f /opt/bepinex/libdoorstop_x64.so "$INSTALL_DIR/" 2>/dev/null || true
  cp -f /opt/bepinex/doorstop_config.ini "$INSTALL_DIR/" 2>/dev/null || true
  cp -a /opt/bepinex/BepInEx/core/. "$INSTALL_DIR/BepInEx/core/" 2>/dev/null || true

  # Ensure default bundled plugins exist without overwriting modified or user-uploaded plugins
  if [[ -d /opt/bepinex/BepInEx/plugins ]]; then
    cp -n /opt/bepinex/BepInEx/plugins/* "$INSTALL_DIR/BepInEx/plugins/" 2>/dev/null || true
  fi

  chmod +x "$INSTALL_DIR/libdoorstop_x64.so" 2>/dev/null || true
fi

if [[ -f "$INSTALL_DIR/valheim_server.x86_64" ]]; then
  chmod +x "$INSTALL_DIR/valheim_server.x86_64"
fi

echo "==> Starting Unified Python Supervisor & FastAPI Panel on port $PANEL_PORT..."
export PATH="/opt/panel_venv/bin:$PATH"
cd /panel
exec /opt/panel_venv/bin/python3 main.py
