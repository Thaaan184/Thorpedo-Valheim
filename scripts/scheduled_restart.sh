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

# 0. Player Guard: Jika masih ada pemain aktif di server dan bukan perintah darurat (--now), tunda restart
if [[ "$SKIP_WAIT" -eq 0 ]]; then
    ACTIVE_PLAYERS=$(docker logs --tail 25 thorpedo-valheim 2>&1 | grep -oE "is active with [0-9]+ player\(s\)" | tail -n 1 | grep -oE "[0-9]+" || echo "0")
    if [[ "$ACTIVE_PLAYERS" -gt 0 ]]; then
        echo "[$(date -Iseconds)] [PLAYER GUARD] Terdeteksi $ACTIVE_PLAYERS ksatria sedang bertualang. Restorasi ditunda demi kenyamanan bermain."
        python3 "${SCRIPT_DIR}/send_discord.py" \
            "🛡️・Restorasi Ditunda: Ksatria Sedang Berjuang" \
            "Allfather menunda hembusan badai karena masih ada ksatria yang sedang menjelajahi alam semesta ($ACTIVE_PLAYERS pemain online). Server tetap berjalan normal!" \
            3066993 \
            '[{"name": "🛡️ Status Alam", "value": "Restorasi ditunda hingga server kosong pada siklus berikutnya.", "inline": false}]' \
            || true
        exit 0
    fi
fi

# 1. Peringatan 5 Menit Sebelum Restorasi
if [[ "$SKIP_WAIT" -eq 0 ]]; then
    echo "[$(date -Iseconds)] Mengirimkan pertanda badai (5 menit sebelum restart)..."
    python3 "${SCRIPT_DIR}/send_discord.py" \
        "⚡・Pertanda Badai Yggdrasil (5 Menit Lagi)" \
        "Angin utara berhembus kencang pertanda Allfather akan meremajakan alam semesta." \
        13849600 \
        '[{"name": "🛡️ Perintah Berteduh", "value": "Segera kembali ke base, dekati api unggun, dan simpan hasil buruan sebelum badai menyapu dunia.", "inline": false}, {"name": "⏳ Waktu Tersisa", "value": "`5 Menit`", "inline": true}, {"name": "🔄 Jadwal Pemulihan", "value": "`01:00` • `07:00` • `13:00` • `19:00 WIB`", "inline": true}]' \
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

# 3. Graceful Stop: Menggunakan Panel REST API jika aktif, atau Docker fallback
echo "[$(date -Iseconds)] Mengirim sinyal graceful restart ke supervisor Thorpedo..."
if curl -s -f http://127.0.0.1:28456/healthz > /dev/null 2>&1; then
    curl -s -X POST http://127.0.0.1:28456/api/actions/execute \
        -H "Content-Type: application/json" \
        -d '{"action": "restart_graceful", "delay_seconds": 0}' || true
else
    cd "$ROOT_DIR"
    docker compose -p thorpedo restart thorpedo || true
fi

echo "[$(date -Iseconds)] Siklus restorasi selesai. Sentinel otomatis akan menyiarkan Join Code baru sesaat lagi."

