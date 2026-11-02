#!/usr/bin/env python3
"""
Thorpedo World Snapshot Backup & GitHub Release Uploader
Packs Valheim world save data and publishes an automated GitHub Release snapshot.
"""
import os
import sys
import glob
import time
import json
import zipfile
import argparse
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

def load_env(env_path):
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip().strip("'\"")
    return env_vars

def create_world_zip(data_dir, output_zip_path):
    worlds_dir = os.path.join(data_dir, "worlds_local")
    if not os.path.isdir(worlds_dir):
        raise FileNotFoundError(f"worlds_local directory not found at {worlds_dir}")

    # Find world directory (BarudakValheim)
    world_dirs = [d for d in os.listdir(worlds_dir) if os.path.isdir(os.path.join(worlds_dir, d)) and not d.startswith(".") and "backup_auto" not in d]
    if not world_dirs:
        raise FileNotFoundError("Active world directory not found in worlds_local")
    world_name = world_dirs[0]
    world_path = os.path.join(worlds_dir, world_name)

    # Ensure parent dir exists
    os.makedirs(os.path.dirname(output_zip_path), exist_ok=True)

    with zipfile.ZipFile(output_zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. Add world chunks
        for root, _, files in os.walk(world_path):
            for file in files:
                abs_path = os.path.join(root, file)
                rel_path = os.path.relpath(abs_path, worlds_dir)
                zf.write(abs_path, os.path.join("worlds_local", rel_path))
        
        # 2. Add .fwl file (root worlds_local/<world_name>.fwl)
        fwl_file = os.path.join(worlds_dir, f"{world_name}.fwl")
        if os.path.isfile(fwl_file):
            zf.write(fwl_file, f"worlds_local/{world_name}.fwl")
        else:
            # Fallback to any backup fwl in worlds_local
            fwl_candidates = glob.glob(os.path.join(worlds_dir, f"{world_name}*.fwl"))
            if fwl_candidates:
                zf.write(fwl_candidates[0], f"worlds_local/{world_name}.fwl")

    size_mb = os.path.getsize(output_zip_path) / (1024 * 1024)
    return size_mb, world_name

def create_github_release(token, repo, tag_name, title, body_text):
    url = f"https://api.github.com/repos/{repo}/releases"
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ThorpedoReleaseBot/1.0"
    }
    payload = {
        "tag_name": tag_name,
        "target_commitish": "main",
        "name": title,
        "body": body_text,
        "draft": False,
        "prerelease": False
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))

def upload_release_asset(token, upload_url, file_path, asset_name):
    # upload_url comes like https://uploads.github.com/repos/.../releases/123/assets{?name,label}
    clean_upload_url = upload_url.split("{")[0]
    target_url = f"{clean_upload_url}?name={asset_name}"
    
    file_size = os.path.getsize(file_path)
    headers = {
        "Authorization": f"token {token}",
        "Content-Type": "application/zip",
        "Content-Length": str(file_size),
        "User-Agent": "ThorpedoReleaseBot/1.0"
    }
    
    with open(file_path, "rb") as f:
        file_data = f.read()

    req = urllib.request.Request(target_url, data=file_data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))

def prune_old_backups(backup_dir, max_days=7):
    if not os.path.isdir(backup_dir):
        return
    now = time.time()
    cutoff = now - (max_days * 86400)
    for fname in os.listdir(backup_dir):
        fpath = os.path.join(backup_dir, fname)
        if (fname.endswith(".zip") or fname.endswith(".tar.gz")) and os.path.isfile(fpath):
            if os.path.getmtime(fpath) < cutoff:
                try:
                    os.remove(fpath)
                    print(f"Pruned old backup: {fname}")
                except Exception as e:
                    print(f"Error pruning {fname}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Thorpedo World Snapshot Backup & GitHub Release")
    parser.add_argument("--data-dir", default="/opt/thorpedo/data", help="Valheim data directory")
    parser.add_argument("--backup-dir", default="/opt/thorpedo/backups", help="Backup archive destination")
    parser.add_argument("--env-file", default="/opt/thorpedo/.env", help="Path to .env file")
    parser.add_argument("--repo", default="Thaaan184/Thorpedo-Valheim", help="GitHub repo")
    args = parser.parse_args()

    env_vars = load_env(args.env_file)
    token = env_vars.get("GITHUB_TOKEN") or os.environ.get("GITHUB_TOKEN")
    repo = env_vars.get("GITHUB_REPO") or args.repo

    wib_tz = timezone(timedelta(hours=7))
    now_wib = datetime.now(wib_tz)
    timestamp_str = now_wib.strftime("%Y%m%d_%H%M%S")
    date_display = now_wib.strftime("%d %b %Y %H:%M WIB")

    zip_filename = f"Thorpedo_World_Save_{timestamp_str}.zip"
    zip_filepath = os.path.join(args.backup_dir, zip_filename)

    print(f"[{now_wib.isoformat()}] Creating world zip snapshot...")
    size_mb, world_name = create_world_zip(args.data_dir, zip_filepath)
    print(f"[{now_wib.isoformat()}] Zip created: {zip_filename} ({size_mb:.2f} MB)")

    tag_name = f"snapshot-{timestamp_str}"
    release_title = f"Thorpedo World Snapshot • {date_display}"
    release_body = (
        f"### 🛡️ Thorpedo Dedicated Server - World Snapshot\n\n"
        f"- **Tanggal Snapshot**: `{date_display}`\n"
        f"- **Ukuran File**: `{size_mb:.2f} MB`\n"
        f"- **Struktur Arsip**: `worlds_local/` (Kompatibel langsung dengan Singleplayer Windows & Linux).\n\n"
        f"#### Panduan Pasang di PC (Singleplayer / Local Co-op):\n"
        f"1. Download `{zip_filename}`.\n"
        f"2. Ekstrak langsung ke folder save Valheim lokal:\n"
        f"   - **Windows**: `%USERPROFILE%\\AppData\\LocalLow\\IronGate\\Valheim\\worlds_local\\`\n"
        f"   - **Linux**: `~/.config/unity3d/IronGate/Valheim/worlds_local/`\n"
        f"3. Buka Valheim → Pilih Karakter → Pilih World Thorpedo.\n"
    )

    if not token:
        print("[!] GITHUB_TOKEN not found in environment or .env. Skipping GitHub Release upload.")
        prune_old_backups(args.backup_dir)
        sys.exit(0)

    try:
        print(f"[{now_wib.isoformat()}] Publishing GitHub Release to {repo}...")
        rel_info = create_github_release(token, repo, tag_name, release_title, release_body)
        rel_url = rel_info.get("html_url", "")
        upload_url = rel_info.get("upload_url", "")
        print(f"[{now_wib.isoformat()}] Release created: {rel_url}")

        print(f"[{now_wib.isoformat()}] Uploading archive asset...")
        upload_release_asset(token, upload_url, zip_filepath, zip_filename)
        print(f"[{now_wib.isoformat()}] Asset uploaded successfully.")

        # Save latest release url to file for monitor/restart scripts
        status_file = os.path.join(args.backup_dir, "latest_release.json")
        with open(status_file, "w") as f:
            json.dump({
                "tag": tag_name,
                "url": rel_url,
                "asset": zip_filename,
                "timestamp": timestamp_str,
                "date_display": date_display
            }, f)

        prune_old_backups(args.backup_dir)
        print(f"[{now_wib.isoformat()}] Snapshot backup & release complete: {rel_url}")
        return rel_url
    except Exception as e:
        print(f"[!] Error during GitHub release: {e}", file=sys.stderr)
        prune_old_backups(args.backup_dir)
        sys.exit(1)

if __name__ == "__main__":
    main()
