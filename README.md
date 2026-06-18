# Shopee Auto-Reply Bot 🤖

Bot Playwright berbasis Docker untuk auto-reply chat Shopee Seller secara otomatis, dengan sesi persisten dan akses VNC untuk intervensi manual.

---

## Struktur Project

```
autochat/
├── bot/
│   ├── main.py           # Bot daemon (Playwright + polling loop)
│   ├── requirements.txt  # Python dependencies
│   ├── Dockerfile        # Image dengan Chromium + noVNC + supervisord
│   └── supervisord.conf  # Process manager (bot, Xvfb, VNC, noVNC)
├── docker-compose.yml    # Orchestration + volume mounts
├── bot-profile/          # (auto-created) Chromium persistent profile
├── logs/                 # (auto-created) Log files
└── .gitignore
```

---

## Cara Pakai

### 1. Build Image

```bash
docker compose build
```

### 2. Login Manual (Pertama Kali)

Karena Shopee memerlukan login sekali di awal:

```bash
# Jalankan dengan VNC aktif (HEADLESS=false sudah default)
docker compose up

# Buka browser → http://localhost:6080
# Login ke Shopee Seller secara manual
# Sesi akan tersimpan di ./bot-profile/
```

### 3. Jalankan sebagai Service Daemon

Setelah login tersimpan, bot berjalan otomatis setiap container distart:

```bash
docker compose up -d
```

Bot akan poll chat baru setiap 5 detik (configurable via `POLL_INTERVAL`).

### 4. Monitor Log

```bash
# Tail log dari host
tail -f logs/bot.log

# Atau langsung dari container
docker compose logs -f shopee-bot
```

---

## Environment Variables

| Variable | Default | Keterangan |
|---|---|---|
| `HEADLESS` | `false` | `true` untuk headless penuh (tanpa VNC) |
| `PROFILE_DIR` | `/data/shopee-profile` | Path profil Chromium di dalam container |
| `POLL_INTERVAL` | `5` | Interval polling chat (detik) |
| `SHOPEE_CHAT_URL` | `https://seller.shopee.co.id/portal/chat` | URL portal chat Shopee Seller |
| `LOG_DIR` | `/data/logs` | Direktori file log |
| `VNC_PASSWORD` | *(empty)* | Password keamanan VNC (opsional, jika kosong VNC terbuka tanpa password) |

---

## Auto-Reply Keywords

Edit dict `AUTO_REPLIES` di [`bot/main.py`](bot/main.py) untuk menambah/ubah template balasan:

```python
AUTO_REPLIES = {
    "harga": "Harga sudah tertera di halaman produk ...",
    "stok":  "Stok masih tersedia ...",
    ...
}
```

---

## Troubleshooting

| Gejala | Kemungkinan Penyebab |
|---|---|
| Bot tidak balas | Shopee ubah DOM — cek log `bot.log` untuk error selector |
| Login loop terus | Profil belum disimpan — ulangi login via VNC |
| Container restart terus | Lihat log supervisord di `logs/supervisord.log` |

---

## Closes

[issue #1](https://github.com/cobacobiy/autochat/issues/1)
