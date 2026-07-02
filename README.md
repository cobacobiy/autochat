# Shopee Auto-Reply Bot 🤖

Bot otomatis berbasis **Playwright**, **Docker**, dan **GitHub Actions** untuk membalas chat Shopee Seller secara otomatis menggunakan AI (**Google Gemini**). Bot ini dilengkapi dengan sesi persisten, akses VNC untuk intervensi manual, dan pipeline CI/CD penuh untuk *deployment* ganda (*Main* & *Preview*).

---

## Arsitektur Sistem

AutoChat dibangun dengan arsitektur modern yang berfokus pada stabilitas, keamanan, dan otomasi:

1. **Core Script (Python & Playwright)**: Bot berjalan di atas mesin Python asinkron (`asyncio`) yang mengendalikan browser *headless* via Playwright. Skrip ini bertugas mensimulasikan klik, *scroll*, dan ketikan layaknya manusia agar tidak terdeteksi sebagai spammer oleh Shopee.
2. **AI Engine (Google Gemini)**: Memanfaatkan model `gemini-flash-latest` melalui REST API Google Generative AI. Gemini membaca riwayat percakapan pembeli dan membandingkannya dengan Pedoman Toko sebelum menghasilkan balasan.
3. **Containerization (Docker & Supervisord)**: Lingkungan *runtime* dibungkus rapi dalam kontainer Docker yang berisi Xvfb (Virtual Framebuffer), x11vnc, dan noVNC. `supervisord` memastikan semua layanan (termasuk skrip bot) tetap menyala dan *auto-restart* jika terjadi *crash*.
4. **CI/CD Pipeline (GitHub Actions)**: 
   - **Main Branch**: Lingkungan produksi stabil.
   - **Preview Environment**: Otomatis menyala setiap ada *Pull Request* dan terisolasi dari produksi. Memungkinkan *testing* fitur baru (seperti aturan AI yang lebih luwes) tanpa merusak bot utama.

---

## Logika & Alur Kerja Bot (Workflow)

Bot didesain untuk bersikap ramah ala *Customer Service* manusia, bukan sekadar mesin penjawab otomatis kaku.

### 🟢 Kapan Bot Membalas Pesan?
1. **Pertanyaan Umum / FAQ:** Jika pembeli menanyakan seputar stok, alamat pengiriman, atau kendala umum yang sudah terdaftar di `store_knowledge.txt`, Gemini akan merangkai jawaban natural berdasarkan data tersebut.
2. **Permintaan Varian/Warna:** Gemini sudah diprogram untuk menyuruh pembeli menuliskan warna atau motif di "Catatan untuk penjual" sebelum *checkout*.
3. **Pengiriman Buru-buru:** Jika pembeli menanyakan pengiriman, Gemini akan menjanjikan pengiriman hari ini untuk Instan/Sameday, atau memohon menunggu antrean untuk Reguler.
4. **Obrolan Santai:** Bot akan merespons sapaan (misal: "Halo min") dengan ramah.

### 🔴 Kapan Bot TIDAK Membalas Pesan (Skip)?
1. **Admin Sudah Turun Tangan:** Jika pesan terakhir di ruang chat dikirim oleh Admin Manusia (Seller), bot akan otomatis mundur dan tidak akan menimpa balasan admin.
2. **AI Benar-benar Tidak Tahu:** Jika pertanyaan *sangat spesifik* (contoh: komplain barang rusak, kendala resi, atau spesifikasi teknis yang tidak ada di pedoman), Gemini tidak akan mengarang bebas (halusinasi).
   - Bot akan membalas dengan sopan: *"Maaf kak, untuk hal itu saya kurang tahu/akan saya cek dulu."*
   - Pesan yang sulit ini akan **dicatat diam-diam** ke dalam file `unanswered_questions.txt` agar admin toko bisa menindaklanjutinya di pagi hari.
3. **Pesan Singkat (Basa-basi):** Kata-kata seperti *"ok", "oke", "baik", "terima kasih"* akan dilewati agar tidak membuang kuota API.
4. **Chat Shopee AI:** Jika Asisten AI bawaan Shopee mengambil alih, bot akan diam.

---

## Struktur Project

```
autochat/
├── .github/workflows/
│   └── ci-cd.yml         # Pipeline CI/CD untuk validasi, build, dan deploy (Main/Preview)
├── bot/
│   ├── main.py           # Bot daemon utama (Playwright + Gemini API)
│   ├── requirements.txt  # Python dependencies (Playwright, httpx, python-dotenv)
│   ├── Dockerfile        # Image dengan Chromium + noVNC + supervisord
│   └── supervisord.conf  # Process manager (bot, Xvfb, VNC, noVNC)
├── docker-compose.yml    # Konfigurasi deploy docker lokal/runner
├── store_knowledge.txt   # File Pedoman Toko / SOP CS untuk jawaban Gemini
├── unanswered_questions.txt # Log histori pertanyaan sulit yang butuh bantuan admin
├── .env                  # Penyimpanan API Key dan variabel environment (JANGAN DI-COMMIT)
└── logs/                 # Folder hasil logging sistem
```

---

## Cara Menjalankan Bot

### 1. Konfigurasi Awal
Salin `.env.example` menjadi `.env` lalu masukkan API Key Google Gemini Anda:
```bash
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-flash-latest
```
Isi juga pedoman dan FAQ toko Anda ke dalam `store_knowledge.txt`.

### 2. Login Shopee (Pertama Kali Saja)
Karena Shopee memerlukan login OTP/QR, jalankan bot secara *interactive* terlebih dahulu:
```bash
docker compose up
```
1. Buka browser dan kunjungi `http://localhost:6080` (atau IP server Anda port `6081` jika Preview).
2. Login ke Shopee Seller Center. Sesi akan tersimpan permanen di volume `shopee-profile`.
3. Matikan kontainer (`Ctrl+C`).

### 3. Jalankan Mode Produksi (Background)
```bash
docker compose up -d
```
Bot akan otomatis berjalan di balik layar dan melakukan *polling* chat baru setiap 10 detik. Jika Anda mengubah `store_knowledge.txt`, bot akan otomatis membaca perubahannya tanpa perlu di-*restart*!
