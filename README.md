# ⚔️ Thorpedo — Valheim Dedicated Server & Control Panel Stack

Stack dedicated server Valheim 1.0+ berbasis container Docker tunggal (**All-in-One Container**) yang mengintegrasikan engine game Valheim dan web management panel (**Thorpedo Control UI**) dalam satu lingkungan terpadu. Dilengkapi pengawas proses (*in-container process supervisor*), fitur anti-hang tanpa perlu `docker compose restart`, PlayFab Crossplay relay, server-side BepInEx mods, automated GitHub Release world snapshots, dan integrasi webhook Discord.

---

## 🏛️ Arsitektur Sistem (All-in-One Container)

```
                 [ Pemain Steam / Console / Tani ]
                                 │
                                 ▼
                     Microsoft PlayFab Relay
                       (6-Digit Join Code)
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│ Host Server (Ubuntu Linux)                                      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │ Container: thorpedo-valheim (All-in-One)                  │  │
│  │                                                           │  │
│  │  ┌─────────────────────────┐   ┌───────────────────────┐  │  │
│  │  │ Web Control Panel       │   │ Valheim Game Engine   │  │  │
│  │  │ • FastAPI + Uvicorn     │   │ • Valheim 1.0+        │  │  │
│  │  │ • Port: 28456 (HTTP)    │   │ • BepInEx 5.4 Loader  │  │  │
│  │  │ • Process Supervisor:   │   │ • Ports: 2456-2457/udp│  │  │
│  │  │   - Graceful (SIGINT)   │──►│ • Managed Subprocess  │  │  │
│  │  │   - Force Kill (SIGKILL)│   │   (In-place restart)  │  │  │
│  │  │   - Ring-buffer console │   └───────────────────────┘  │  │
│  │  └─────────────────────────┘                              │  │
│  └─────────────┬─────────────────────────────┬───────────────┘  │
│                │                             │                  │
│  ┌─────────────▼─────────────┐ ┌─────────────▼───────────────┐  │
│  │ Persistent Bind Mounts    │ │ Cloudflare Tunnel           │  │
│  │ • ./data -> /config       │ │ • cloudflared tunnel        │  │
│  │ • ./plugins -> /bepinex   │ │ • Zero-Trust Access         │  │
│  │ • valheim-data (Volume)   │ │ • Ekspor Port 28456 Web     │  │
│  └───────────────────────────┘ └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## ⚡ Keunggulan Arsitektur All-in-One

1. **In-Place Restart Tanpa Docker Restart**:
   - Panel web berjalan sebagai supervisor proses game (`valheim_server.x86_64`) via Python subprocess.
   - Melakukan restart server game **tidak mematikan container Docker**. Koneksi HTTP panel tetap hidup 100%.
2. **Fitur Penyelamat Saat Game Hang / Freeze**:
   - **Graceful Restart**: Mengirim sinyal `SIGINT` ke engine game agar data save dan chunk dunia tersimpan rapi ke disk sebelum proses dimuat ulang.
   - **Force Kill (`kill -9`)**: Tombol darurat di panel untuk mematikan paksa engine game seketika saat thread socket membeku atau terjadi deadlock, lalu menyalakan ulang engine dalam hitungan detik.
   - **Reset Lobi PlayFab**: Memulai ulang sesi PlayFab untuk memperbarui pendaftaran lobi dan merotasi Join Code.
3. **Console Stream Real-Time**:
   - Output stdout/stderr engine game ditangkap langsung ke in-memory ring-buffer panel. Tab **Console** menampilkan log aktivitas, join, save, hingga kill-feed secara langsung.

---

## 🖥️ Fitur Web Management Panel (Port 28456)

Panel kontrol web Thorpedo dirancang dengan antarmuka gelap responsif bergaya taktikal (*JetBrains Mono + Inter*):
- **Ringkasan Metrik**: Uptime engine, CPU Usage %, Memory RSS (MB/GB), dan sisa ruang disk host.
- **Kartu Koneksi**: Status lobi PlayFab, 6-digit Join Code aktif, IP langsung, port game, dan tombol copy 1-klik.
- **Roster Ksatria**: Memantau daftar karakter yang sedang aktif di dunia, status admin, dan durasi sesi bermain.
- **Tindakan Cepat**: Menu aksi restart bertahap (instan, 60 detik, 5 menit dengan notifikasi), simpan paksa (*force save*), dan kill paksa.
- **Manajemen Dunia**: Tab penyesuaian aturan modifier dunia (*resources, portals, raids, combat, death penalty*) langsung dari web.
- **Snapshot & Rollback**: Manajemen backup berkala dan rollback data dunia yang aman.
- **Manajemen Pemain**: Mengatur daftar admin (`adminlist.txt`) dan daftar blokir (`bannedlist.txt`).

---

## 🚀 Panduan Setup & Menjalankan

### 1. Kloning Repositori
```bash
git clone https://github.com/Thaaan184/Thorpedo-Valheim.git /home/ids/projects/Thorpedo-Valheim
cd /home/ids/projects/Thorpedo-Valheim
```

### 2. Konfigurasi Variabel Lingkungan (`.env`)
Salin file konfigurasi contoh dan sesuaikan password serta port:
```bash
cp .env.example .env
nano .env
```
Parameter penting:
- `SERVER_NAME`: Nama server yang tampil di server browser (misal: `Thorpedo`).
- `WORLD_NAME`: Nama world save (misal: `BarudakValheim`).
- `SERVER_PASSWORD`: Password masuk server (minimal 5 karakter).
- `PANEL_PORT`: Port HTTP panel web (default: `28456`).
- `SERVER_PORT`: Port UDP server Valheim (default: `2456`).
- `CROSSPLAY`: Set ke `true` untuk mengaktifkan PlayFab Join Code (lintas Steam, Game Pass, dan client tani).

### 3. Build & Jalankan Container
```bash
docker compose build
docker compose up -d
```

### 4. Periksa Status Layanan
```bash
# Periksa status container
docker compose ps

# Periksa log panel dan game engine
docker compose logs -f thorpedo
```

### 5. Akses Panel Web
Buka browser dan arahkan ke IP server host:
```
http://<IP_HOST>:28456/
```
*(Untuk akses jarak jauh yang aman, arahkan port `28456` melalui Cloudflare Tunnel / Cloudflare Access)*.

---

## 🛡️ Server-Side Mods (Zero Client Setup)

Server mengoperasikan mod mandiri di sisi server via BepInEx. Pemain **tidak perlu memasang mod apa pun** di PC masing-masing untuk bergabung:

1. **`NoCheatFlag.dll`**: Membersihkan status `m_cheated` dari semua item yang jatuh di alam dan peti penyimpanan, mencegah bug infeksi cheat bawaan Valheim 1.0 yang dapat mengunci progres achievement pemain.
2. **`ScheduledMessages.dll`**: Mengirimkan peringatan teks langsung di tengah layar semua pemain di dalam game sebelum siklus pemulihan alam dimulai (T-10m, T-5m, dan T-1m).
3. **`Gjallarhorn.dll`**: Kill-feed & death announcer Nordik server-side. Menyiarkan kabar gugurnya ksatria (beserta monster/penyebab kematian) dan tumbangnya boss dunia langsung di chat dan banner tengah layar.
4. **`WorkbenchLove.dll`**: Memperluas radius perlindungan dan pencegah spawn monster meja kerja (*workbench protection & anti-mob spawn radius*) dari 20m menjadi 40m.
5. **`ServerDevcommands.dll`**: Membuka kunci perintah administratif darurat untuk admin server (*skip time, forcedelete glitch item, stopevent raid, save paksa*).
6. **`CrossplayJoinCodeFix.dll`**: Mencegah bug NullReferenceException bawaan Valheim saat inisialisasi lobi PlayFab, memastikan Join Code selalu berhasil resolve di cloud Microsoft.
7. **`NetworkPerformanceSystem.dll`**: Optimasi throughput sinkronisasi ZDO, kompensasi latensi, dan ukuran window paket UDP antar-pemain.

---

## 🎮 Panduan Masuk Game (Client Join Guide)

### 1. Pemain Steam Original
1. Buka Steam dan jalankan Valheim.
2. Di menu utama, klik **Join Game** → klik tab **Add Server**.
3. Masukkan **Join Code (6-digit)** aktif yang tertera di panel web atau channel Discord.
4. Masukkan password server saat diminta.

### 2. Pemain Non-Steam / Spacewar / Tani
1. Pastikan aplikasi Steam di PC Anda **sudah login dan berjalan di background** (akun gratis apa saja).
2. Jalankan game via `valheim.exe` atau launcher bawaan patch Online-Fix (status Steam akan terdeteksi memainkan *Spacewar* / AppID 480).
3. Di menu utama Valheim: klik **Join Game** → klik **Add Server** (⚠️ **PERHATIAN**: Klik *Add Server*, **JANGAN** klik *Join IP*).
4. Masukkan **Join Code (6-digit)** aktif dari panel web, lalu masukkan password.

---

## 📁 Struktur Direktori Repositori

```
.
├── Dockerfile                    # Multi-stage image: Valheim Engine + Python 3 + FastAPI
├── compose.yaml                  # Definisi Docker Compose All-in-One stack
├── entrypoint_allinone.sh        # Bootstrapper SteamCMD, BepInEx, dan launcher panel Uvicorn
├── .env.example                  # Template variabel lingkungan
├── panel/
│   ├── main.py                   # REST API backend FastAPI & router
│   ├── engine_manager.py         # Supervisor proses game (SIGINT/SIGKILL, log buffer, PlayFab)
│   ├── services.py               # Layer bisnis sistem, file config, snapshot, dan modifier
│   ├── auth.py                   # Otentikasi sesi & token
│   ├── templates/
│   │   ├── index.html            # Antarmuka web baru Thorpedo Valheim Control
│   │   └── login.html            # Antarmuka login form
│   └── tests/
│       ├── test_engine_manager.py # Pengujian unit supervisor proses
│       └── test_api.py           # Pengujian endpoint REST API
├── config/
│   └── scheduledmessages.cfg     # Konfigurasi pesan otomatis dalam game
├── plugins/                      # Plugin server-side BepInEx (.dll)
├── scripts/
│   ├── scheduled_restart.sh      # Skrip backup berkala dan pemulihan otomatis
│   └── backup_to_release.py      # Pengunggah snapshot zip ke GitHub Release
└── README.md
```
