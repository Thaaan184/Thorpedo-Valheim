#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ENV_FILE="${ROOT_DIR}/.env"

SKIP_WAIT=0
if [[ "${1:-}" == "--now" ]]; then
    SKIP_WAIT=1
fi

echo "[$(date -Iseconds)] Memulai siklus restorasi berkala server Thorpedo..."

# 1. Peringatan 5 Menit Sebelum Restorasi
if [[ "$SKIP_WAIT" -eq 0 ]]; then
    echo "[$(date -Iseconds)] Mengirimkan pertanda badai (5 menit sebelum restart)..."
    python3 "${SCRIPT_DIR}/send_discord.py" \
        "⚡・Pertanda Badai Yggdrasil (5 Menit Lagi)" \
        "Angin utara berhembus kencang pertanda Allfather akan meremajakan alam semesta." \
        13849600 \
        '[{"name": "🛡️ Perintah Berteduh", "value": "Segera kembali ke base, dekati api unggun, dan simpan hasil buruan sebelum badai menyapu dunia.", "inline": false}, {"name": "⏳ Waktu Tersisa", "value": "`5 Menit`", "inline": true}, {"name": "🔄 Jadwal Pemulihan", "value": "`06:00` • `12:00` • `18:00` • `00:00 WIB`", "inline": true}]' \
        || true

    echo "[$(date -Iseconds)] Menunggu 300 detik menuju saat restorasi..."
    sleep 300
fi

# 2. Pengumuman Eksekusi Restorasi
echo "[$(date -Iseconds)] Mengumumkan proses restorasi dimulai..."
python3 "${SCRIPT_DIR}/send_discord.py" \
    "🛡️・Penyucian Alam Semesta Dimulai" \
    "Seluruh jejak dunia diabadikan ke dalam batu rune (Backup Aman). Server sedang dimuat ulang." \
    9841965 \
    '[{"name": "📦 Status Ritual", "value": "Menyimpan Dunia & Memulai Ulang Container", "inline": true}, {"name": "⏳ Estimasi Jeda", "value": "`~1-2 Menit`", "inline": true}, {"name": "ℹ️ Catatan", "value": "Kunci gerbang (Join Code) baru akan segera disiarkan Hugin begitu langit kembali cerah.", "inline": false}]' \
    || true

# 3. Graceful Stop: Docker mengirim SIGINT, Valheim flush world save ke disk sebelum container mati
echo "[$(date -Iseconds)] Menghentikan container game secara aman (SIGINT world flush)..."
cd "$ROOT_DIR"
docker compose -p thorpedo stop -t 60 valheim

# 4. Buat snapshot zip dan upload ke GitHub Release
echo "[$(date -Iseconds)] Menjalankan snapshot backup & push GitHub Release..."
python3 "${SCRIPT_DIR}/backup_to_release.py" \
    --data-dir "${ROOT_DIR}/data" \
    --backup-dir "${ROOT_DIR}/backups" \
    --env-file "${ENV_FILE}" \
    || echo "[WARN] Gagal membuat GitHub Release, melanjutkan restart..."

# 5. Nyalakan kembali container game
echo "[$(date -Iseconds)] Menyalakan kembali Valheim dedicated server..."
docker compose -p thorpedo -f "${ROOT_DIR}/compose.yaml" up -d valheim

echo "[$(date -Iseconds)] Siklus restorasi selesai. Monitor akan menyiarkan Join Code baru sesaat lagi."
