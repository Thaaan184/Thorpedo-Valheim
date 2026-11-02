# ⚔️ Thorpedo — Valheim Dedicated Server Stack

Persistent, containerized Valheim Dedicated Server running on Docker with PlayFab Crossplay, server-side BepInEx mods, automated GitHub Release world snapshots, and real-time Discord monitoring.

---

## 🏛️ Arsitektur Sistem

```
                 [ Pemain Steam / Console / Tani ]
                                 │
                                 ▼
                     Microsoft PlayFab Relay
                       (6-Digit Join Code)
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Target Ubuntu Host                                              │
│                                                                 │
│  ┌───────────────────────────┐     ┌─────────────────────────┐  │
│  │ Container: valheim        │     │ Container: playit       │  │
│  │ - Engine: Valheim 1.0+    │     │ - Host networking       │  │
│  │ - Loader: BepInEx 5.4     │◄───►│ - UDP Tunnels           │  │
│  │ - Mods:                   │     │   (2456 / 2457)         │  │
│  │   • NoCheatFlag           │     └─────────────────────────┘  │
│  │   • ScheduledMessages     │                                  │
│  │   • NetworkPerfSystem     │                                  │
│  └─────────────┬─────────────┘                                  │
│                │                                                │
│  ┌─────────────▼─────────────┐     ┌─────────────────────────┐  │
│  │ Persistent Volumes        │     │ Host Systemd Daemon     │  │
│  │ • /data (worlds_local)    │◄───►│ • valheim_monitor.py    │──┐
│  │ • /backups (archive .zip) │     │ • scheduled_restart.sh  │  │
│  └─────────────┬─────────────┘     └─────────────────────────┘  │
└────────────────┼────────────────────────────────────────────────┼┘
                 │                                                │
                 ▼                                                ▼
     [ GitHub Releases API ]                          [ Discord Webhooks ]
   (Automated World Snapshots)                       (Hugin Lore Watcher)
```

---

## ⚙️ Spesifikasi & Aturan Dunia (World Rules)

- **Nama Server**: `Thorpedo`
- **Networking**: PlayFab Crossplay Relay (`CROSSPLAY=true`)
- **Password Gerbang**: Sesuai konfigurasi `.env`
- **World Modifiers Aktif**:
  - `resources=muchmore`: Hasil tebang kayu, tambang ore, dan drop hasil buruan **2x lipat**.
  - `portals=casual`: Semua bijih tambang (copper, tin, iron, silver, blackmetal, flametal) dan dragon egg **bebas dibawa melewati portal kayu biasa**.
  - `playerevents`: Serbuan monster ke markas (*base raid*) menyesuaikan pencapaian individu pemain di sekitar markas, melindungi markas pemula dari serangan monster tingkat tinggi.

---

## 🚀 Panduan Masuk Game (Client Join Guide)

### 1. Pemain Steam Original
1. Buka Steam dan jalankan Valheim.
2. Di menu utama, klik **Join Game** → klik tab **Add Server**.
3. Masukkan **Join Code (6-digit)** aktif yang disiarkan di channel Discord.
4. Masukkan password server saat diminta.

### 2. Pemain Non-Steam / Spacewar / Tani
1. Pastikan aplikasi Steam di PC Anda **sudah login dan berjalan di background** (akun gratis apa saja).
2. Jalankan game via `valheim.exe` atau launcher 1-click bawaan patch Online-Fix (status Steam akan terdeteksi memainkan *Spacewar* / AppID 480).
3. Di menu utama Valheim: klik **Join Game** → klik **Add Server** (⚠️ **PERHATIAN**: Klik *Add Server*, **JANGAN** klik *Join IP*).
4. Masukkan **Join Code (6-digit)** aktif dari Discord, lalu masukkan password.

---

## 🛡️ Server-Side Mods (Zero Client Setup)

Server mengoperasikan mod mandiri di sisi server via BepInEx. Pemain **tidak perlu memasang mod apa pun** di PC masing-masing untuk bergabung:

1. **`NoCheatFlag.dll`**: Membersihkan status `m_cheated` dari semua item yang jatuh di alam dan peti penyimpanan, mencegah bug infeksi cheat bawaan Valheim 1.0 yang dapat mengunci progres achievement pemain.
2. **`ScheduledMessages.dll`**: Mengirimkan peringatan teks langsung di tengah layar semua pemain di dalam game sebelum siklus pemulihan alam dimulai (T-10m, T-5m, dan T-1m).
3. **`Gjallarhorn.dll`**: Kill-feed & death announcer Nordik server-side. Menyiarkan kabar gugurnya ksatria (beserta monster/penyebab kematian) dan tumbangnya boss dunia langsung di chat dan banner tengah layar.
4. **`WorkbenchLove.dll`**: Memperluas radius perlindungan dan pencegah spawn monster meja kerja (workbench protection & anti-mob spawn radius) dari 20m menjadi 40m.
5. **`ServerDevcommands.dll`**: Membuka kunci perintah administratif darurat untuk admin server (skip time, forcedelete glitch item, stopevent raid, save paksa).
6. **`NetworkPerformanceSystem.dll`**: Optimasi throughput sinkronisasi ZDO, kompensasi latensi, dan ukuran window paket UDP antar-pemain.

---

## 🔄 Siklus Pemulihan Alam Terjadwal (Tiap Jam 6)

Server menjalankan pembersihan cache, defragmentasi ZDO, dan kompresi data rutin tiap jam 6 pada pukul:
**`06:00` & `18:00 WIB`**

### Urutan Prosedur:
1. **Peringatan Dalam Game**: Banner teks peringatan muncul di layar pemain 10 menit, 5 menit, dan 1 menit sebelum server mati.
2. **Pemberitahuan Discord**: Bot Hugin membagikan kartu peringatan badai.
3. **Graceful World Flush**: Host mengeksekusi `docker compose stop -t 60 valheim`. Engine Valheim menerima sinyal `SIGINT` dan mem-flush seluruh chunk dunia yang aktif ke disk sebelum mematikan proses container.
4. **Automated GitHub Release Snapshot**: Script `backup_to_release.py` membungkus file save aktif ke format `.zip` kompatibel Windows/Linux, membuat rilis baru bertag `snapshot-YYYYMMDD_HHMM`, dan mengunggah arsip ke tab [Releases](../../releases).
5. **Cold Reboot**: Container dinyalakan kembali, PlayFab menerbitkan 6-digit Join Code baru, dan Hugin menyiarkan kode baru beserta tautan snapshot dunia ke Discord.

---

## 📁 Struktur Direktori Repositori

```
.
├── compose.yaml                  # Definisi stack container Valheim & Playit
├── .env.example                  # Template variabel lingkungan
├── config/
│   └── scheduledmessages.cfg     # Jadwal pesan pengumuman in-game
├── scripts/
│   ├── valheim_monitor.py        # Host daemon pemantau event & webhook Discord
│   ├── scheduled_restart.sh      # Skrip automasi siklus restart 6-jam
│   ├── backup_to_release.py      # Pembuat snapshot zip & uploader GitHub Release
│   └── send_discord.py           # CLI pengirim embed Discord webhook
└── README.md
```

---

## 🛠️ Panduan Deployment Mandiri

### 1. Kloning dan Konfigurasi
```bash
git clone https://github.com/Thaaan184/Thorpedo-Valheim.git /opt/thorpedo
cd /opt/thorpedo
cp .env.example .env
chmod 600 .env
nano .env
```

### 2. Jalankan Server
```bash
docker compose up -d
docker compose logs -f valheim
```

### 3. Pasang Systemd Watcher di Host
```bash
sudo cp scripts/thorpedo-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now thorpedo-monitor.service
```

### 4. Jadwalkan Crontab Host
```cron
# Eksekusi restart setiap pukul 06:00 dan 18:00 WIB (UTC+7 -> 22:55 dan 10:55 UTC)
55 10,22 * * * /opt/thorpedo/scripts/scheduled_restart.sh >> /opt/thorpedo/logs/restart.log 2>&1
```
