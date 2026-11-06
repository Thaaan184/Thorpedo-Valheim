#!/usr/bin/env bash
set -eo pipefail

SERVER_NAME="${SERVER_NAME:-Thorpedo}"
WORLD_NAME="${WORLD_NAME:-BarudakValheim}"
SERVER_PASSWORD="${SERVER_PASSWORD:-z315pn}"
SERVER_PORT="${SERVER_PORT:-2456}"
SERVER_PUBLIC="${SERVER_PUBLIC:-0}"
CROSSPLAY="${CROSSPLAY:-true}"
WORLD_SEED="${WORLD_SEED:-z315pn}"
INSTALL_DIR="${INSTALL_DIR:-/valheim}"
SAVE_DIR="${SAVE_DIR:-/config}"
PANEL_PORT="${PANEL_PORT:-28456}"
UPDATE_ON_START="${UPDATE_ON_START:-true}"
VALIDATE_ON_UPDATE="${VALIDATE_ON_UPDATE:-false}"
BEPINEX="${BEPINEX:-true}"

echo "=========================================================="
echo "    THORPEDO VALHEIM ALL-IN-ONE CONTAINER STARTING        "
echo "=========================================================="
echo "Server Name : $SERVER_NAME"
echo "World Name  : $WORLD_NAME"
echo "Port (Game) : $SERVER_PORT (UDP)"
echo "Port (Panel): $PANEL_PORT (HTTP)"
echo "Crossplay   : $CROSSPLAY"
echo "BepInEx     : $BEPINEX"
echo "=========================================================="

mkdir -p "$SAVE_DIR/worlds_local" "$INSTALL_DIR"

# Check & create world seed if world does not exist
if [[ -n "$WORLD_SEED" ]]; then
  fwl="$SAVE_DIR/worlds_local/$WORLD_NAME.fwl"
  if [[ -e "$fwl" || -d "$SAVE_DIR/worlds_local/$WORLD_NAME" ]]; then
    echo "==> Existing world '$WORLD_NAME' found. Preserving all existing world chunks and metadata."
  else
    echo "==> Creating new world '$WORLD_NAME' with seed '$WORLD_SEED'..."
    if command -v mkworld.sh >/dev/null 2>&1; then
      mkworld.sh "$WORLD_NAME" "$WORLD_SEED" "$fwl"
    fi
  fi
fi

# SteamCMD updates
if [[ "$UPDATE_ON_START" == "true" || ! -x "$INSTALL_DIR/valheim_server.x86_64" ]]; then
  echo "==> Checking/updating Valheim Dedicated Server files (App ID 896660)..."
  update_args=(+force_install_dir "$INSTALL_DIR" +login anonymous +app_update 896660)
  [[ "$VALIDATE_ON_UPDATE" == "true" ]] && update_args+=(validate)

  # Retry loop for SteamCMD
  for attempt in 1 2 3; do
    if steamcmd "${update_args[@]}" +quit; then
      echo "==> SteamCMD update completed successfully."
      break
    fi
    echo "==> SteamCMD attempt $attempt failed; retrying in 5s..."
    sleep 5
  done
fi

# BepInEx setup
if [[ "$BEPINEX" == "true" && -d /opt/bepinex ]]; then
  echo "==> Synchronizing BepInEx loader and plugins into $INSTALL_DIR..."
  rm -rf "$INSTALL_DIR/BepInEx/core" "$INSTALL_DIR/BepInEx/plugins" "$INSTALL_DIR/doorstop_libs"
  mkdir -p "$INSTALL_DIR/BepInEx/config" "$INSTALL_DIR/BepInEx/plugins"
  cp -a /opt/bepinex/BepInEx/core /opt/bepinex/BepInEx/plugins "$INSTALL_DIR/BepInEx/" 2>/dev/null || true
  cp -a /opt/bepinex/doorstop_libs "$INSTALL_DIR/doorstop_libs" 2>/dev/null || true
  cp -an /opt/bepinex/BepInEx/config/. "$INSTALL_DIR/BepInEx/config/" 2>/dev/null || true

  # Also copy user-mounted plugins from /opt/bepinex/BepInEx/plugins if mounted as volume
  for plugin in "$INSTALL_DIR"/BepInEx/plugins/*.dll; do
    if [[ -f "$plugin" ]]; then
      echo "    Loaded plugin: $(basename "$plugin")"
    fi
  done
fi

echo "==> Launching Web Management Panel on port $PANEL_PORT..."
exec /opt/panel_venv/bin/uvicorn main:app --host 0.0.0.0 --port "$PANEL_PORT" --app-dir /panel
