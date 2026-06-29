# Shopee Auto-Reply Bot 🤖

Bot Playwright berbasis Docker untuk auto-reply chat Shopee Seller menggunakan AI (Gemini / Ollama lokal), dengan sesi persisten dan akses VNC untuk intervensi manual.

---

## Alur Kerja Bot (Workflow Saat Ini)

Bot bekerja secara otomatis merespons pesan masuk dari pembeli berdasarkan pedoman toko yang telah ditentukan (`store_knowledge.txt`). Berikut adalah detail kapan bot akan membalas dan kapan tidak:

### 🟢 Kapan Bot Membalas Pesan?
1. **Pertanyaan Sesuai Pedoman Toko:** Bot akan membalas menggunakan jawaban AI (Gemini/Ollama) jika pertanyaan pembeli sesuai atau ada jawabannya di dalam file `store_knowledge.txt`.
2. **Pembeli Mengetik Pesan Baru:** Jika pembeli mengirim pesan teks biasa atau pesan gambar (disertai teks penjelasan sebelumnya), bot akan memproses pesan tersebut.
3. **Belum Mencapai Batas Harian:** Bot akan terus membalas selama jumlah balasan harian belum melampaui `MAX_DAILY_REPLIES` (default: 500).

### 🔴 Kapan Bot TIDAK Membalas Pesan (Skip)?
1. **Admin Sudah Membalas:** Jika pesan terakhir di chat dikirim oleh Admin Manusia (Seller), bot akan mundur dan tidak akan menimpa/double reply.
2. **AI Tidak Tahu Jawabannya:** Jika pertanyaan pembeli **tidak ada** di dalam `store_knowledge.txt`, AI akan menghasilkan output "TIDAK TAHU".
   - Bot **TIDAK AKAN** mengirimkan pesan "Tidak Tahu" atau mengarang bebas.
   - Pertanyaan pembeli tersebut akan dicatat secara diam-diam ke dalam file `unanswered_questions.txt` agar admin manusia bisa mereview dan menjawabnya secara manual.
3. **Pesan Singkat (Basa-basi):** Bot otomatis mengabaikan pesan non-pertanyaan yang singkat seperti *"ok", "oke", "baik", "terima kasih", "makasih", "siap", "sip"*, agar tidak membuang-buang token AI.
4. **Chat Diambil Alih Asisten AI Shopee:** Jika chat sudah ditangani oleh Asisten AI bawaan Shopee, bot tidak akan ikut campur.
5. **Chat Telah Diakhiri:** Jika chat sudah ditutup otomatis oleh sistem Shopee.

---

## Hal-Hal yang Perlu Dilakukan Bot (Spesifikasi Teknis)
Berikut daftar tugas yang dilakukan bot di belakang layar agar semuanya jelas:
1. **Auto-Reload Knowledge:** Bot membaca ulang file `store_knowledge.txt` secara berkala tanpa perlu restart.
2. **Deteksi Pesan Seller:** Memeriksa struktur DOM Shopee (warna background, posisi gelembung chat, tag `isSeller`) untuk memastikan pesan dikirim oleh admin manusia atau bot.
3. **Ekstraksi Pesan Pembeli:** Mencari pesan terakhir dari pembeli di halaman utama atau lewat pop-up "Riwayat Chat" jika pesannya terlalu panjang.
4. **Proteksi Halusinasi AI:** Memfilter awalan aneh dari AI (seperti "Anda: ", "Jawaban: ") dan mendisiplinkan AI agar **wajib** menjawab `TIDAK TAHU` jika informasi tidak ada di pedoman.
5. **Auto-Cache (Pencegah Spam):** Menyimpan cache `username + potongan pesan` agar bot tidak merespons pertanyaan yang sama berulang kali dalam waktu berdekatan.
6. **Mencatat Pertanyaan Sulit:** Menambahkan log ke `unanswered_questions.txt` beserta *timestamp* dan *username* pembeli jika AI angkat tangan.
7. **Bypass UI Shopee:** Mengetik ke dalam elemen `contenteditable` menggunakan simulasi *keyboard typing* karena Shopee memblokir input standar via `value`.

---

## Struktur Project

```
autochat/
├── bot/
│   ├── main.py           # Bot daemon utama (Playwright + polling loop)
│   ├── requirements.txt  # Python dependencies
│   ├── Dockerfile        # Image dengan Chromium + noVNC + supervisord
│   └── supervisord.conf  # Process manager (bot, Xvfb, VNC, noVNC)
├── docker-compose.yml    # Orchestration + volume mounts
├── store_knowledge.txt   # File Pedoman Toko untuk jawaban AI
├── unanswered_questions.txt # Log pertanyaan yang gagal dijawab AI
├── bot-profile/          # (auto-created) Chromium persistent profile
└── logs/                 # (auto-created) Log files
```

---

## Cara Pakai

### 1. Konfigurasi
- Pastikan mengisi API Key Gemini di variabel `GEMINI_API_KEY` (di environment atau kode) agar AI pintar bisa menyala.
- Isi FAQ / panduan toko di file `store_knowledge.txt`.

### 2. Build Image
```bash
docker compose build
```

### 3. Login Manual (Pertama Kali)
Karena Shopee memerlukan login OTP di awal:
```bash
docker compose up
# Buka browser → http://localhost:6080
# Login ke Shopee Seller secara manual
# Sesi akan tersimpan di profil bot
```

### 4. Jalankan sebagai Service
Setelah login sukses:
```bash
docker compose up -d
```
Bot akan polling setiap 5 detik. Pantau logs dengan:
```bash
docker compose logs -f shopee-bot
```

---

## Kustomisasi Pengetahuan AI
Anda cukup mengedit file `store_knowledge.txt` kapan saja tanpa perlu me-restart bot. Contoh format:
```text
T: Dikirim dari mana kak? / drmn / darimana / dikirim drmn
J: Pengiriman dari Penjaringan, Jakarta Utara kak.

T: Barang ready?
J: Semua barang yang variannya bisa di-klik di etalase berarti ready stock kak, silakan diorder..
```
Jika ada pembeli yang bertanya di luar pedoman ini, bot akan membiarkannya dan mencatatnya di `unanswered_questions.txt`.
