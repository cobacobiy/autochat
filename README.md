# Shopee Auto-Reply AI Bot 🤖🛒

Shopee Auto-Reply AI Bot adalah sebuah program otomatisasi cerdas yang bertugas untuk membalas pesan pembeli di Shopee Seller Center secara luwes dan natural, layaknya Customer Service (CS) manusia.

Bot ini dibuat menggunakan **Python**, **Playwright**, dan mesin LLM lokal (terutama **Ollama** dengan opsi Gemini/Claude) untuk memberikan respons pintar berdasarkan basis pengetahuan (*knowledge base*) toko Anda.

---

## ✨ Fitur Utama (Terbaru)

- **Smart AI Integration (Ollama Default)**: Menggunakan kecerdasan buatan untuk merespon chat pembeli secara luwes, tidak kaku, dan sangat menyesuaikan dengan pedoman di *knowledge base* toko. Tidak akan menolak menjawab selama masih masuk akal ala CS manusia.
- **Anti-Bot Detection (Stealth Mode)**: Dirancang sangat hati-hati agar tidak terdeteksi oleh sistem keamanan modern Shopee (bebas dari error "Terjadi Kesalahan"):
  - **No-Click History**: Ekstraksi riwayat chat murni melalui bacaan elemen layar (DOM) pada kotak *preview*, **tanpa** melakukan klik buatan pada tombol riwayat.
  - **Jeda Manusiawi (Human Delay)**: Memiliki jeda simulasi klik layaknya reaksi manusia asli (angka acak antara 1,5 hingga 4,5 detik).
  - **Auto-Reload & Anti-Crash**: Jika terjadi halaman *blank* atau error dari Shopee, bot tidak akan klik paksa melainkan otomatis melakukan *refresh* (reload) halaman secara natural.
- **Knowledge Base Driven**: Jawaban bot bersumber dari file `store_knowledge.txt`. Bot tidak akan mengarang spesifikasi produk yang tidak ia ketahui.
- **Unanswered Questions Logger**: Jika bot menemui pertanyaan yang sangat spesifik dan di luar kemampuannya, ia akan membalas dengan sopan lalu mencatat pertanyaan tersebut ke file `unanswered_questions.txt` agar bisa ditangani manual oleh Admin.
- **Smart Filtering**: Otomatis mengabaikan pesan sistem (seperti "Asisten AI Toko") dan fokus hanya pada pertanyaan asli pembeli yang terekstrak dari riwayat chat.

---

## 🛠️ Persyaratan Sistem

- **Python 3.10+**
- **Ollama** (terinstall di lokal untuk penggunaan LLM *offline* - rekomendasi model: `qwen2.5:3b`).
- **Docker** (Opsional, jika ingin menjalankan via container CI/CD).

---

## 🚀 Cara Instalasi & Penggunaan

### Menggunakan Python (Lokal)
1. **Clone repository ini:**
   ```bash
   git clone https://github.com/cobacobiy/autochat.git
   cd autochat
   ```

2. **Buat Virtual Environment & Install Dependencies:**
   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # Mac/Linux
   source .venv/bin/activate
   
   pip install -r bot/requirements.txt
   playwright install chromium
   ```

3. **Konfigurasi Lingkungan (`.env`):**
   Ubah atau buat file `.env` di folder utama:
   ```env
   AI_PROVIDER=ollama
   OLLAMA_URL=http://localhost:11434
   OLLAMA_MODEL=qwen2.5:3b
   
   # Untuk Gemini (opsional jika AI_PROVIDER=gemini)
   # GEMINI_API_KEY=your_api_key_here
   
   HEADLESS=false # Ubah ke 'true' jika ingin berjalan di latar belakang
   MAX_DAILY_REPLIES=5000
   MAX_CACHE_SIZE=1000
   ```

4. **Siapkan Basis Pengetahuan:**
   Isi file `store_knowledge.txt` dengan informasi toko, detail pengiriman, dan katalog produk Anda agar bot bisa mempelajarinya secara dinamis.

5. **Jalankan Bot:**
   ```bash
   python bot/main.py
   ```
   *Catatan: Pada saat pertama kali dijalankan, jendela browser Chromium akan terbuka. Lakukan login ke akun Shopee Seller Center secara manual. Setelah login berhasil, bot akan mengambil alih pekerjaan.*

### Menggunakan Docker
Jika Anda ingin menjalankannya tanpa pusing dengan instalasi Python, dan ingin fitur VNC (Akses Layar Jarak Jauh):
```bash
docker compose up -d
```
*Gunakan web browser (melalui port `6080` yang diatur di docker-compose) untuk masuk via noVNC dan melakukan login Shopee manual pertama kali.*

### Menjalankan Multi-Bot (Lebih dari 1 Toko)
Jika Anda memiliki beberapa toko Shopee, Anda dapat menjalankan bot secara paralel dengan menduplikasi service di `docker-compose.yml`. Pastikan setiap bot memiliki volume dan port VNC yang berbeda. Contoh:
```yaml
services:
  bot-toko-a:
    build: ./bot
    volumes:
      - bot-toko-a-profile:/data/shopee-profile
      - bot-toko-a-logs:/data/logs
    ports:
      - "6080:6080"
  bot-toko-b:
    build: ./bot
    volumes:
      - bot-toko-b-profile:/data/shopee-profile
      - bot-toko-b-logs:/data/logs
    ports:
      - "6081:6080"
```
Login ke VNC secara terpisah (port 6080 untuk Toko A, 6081 untuk Toko B) untuk setup awal.

### Menggunakan GPU (Docker Passthrough)
Secara default, container Docker sudah dikonfigurasi untuk menggunakan GPU NVIDIA jika tersedia (lihat `docker-compose.yml` baris 26-32). Pastikan Anda sudah menginstal [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html). Cek dengan menjalankan `nvidia-smi`.

---

## 📂 Struktur Project

```
autochat/
├── .github/workflows/
│   └── ci-cd.yml         # Pipeline CI/CD untuk otomatisasi update
├── bot/
│   ├── main.py           # Logika utama bot (scraping, auto-reply, stealth bypass)
│   ├── requirements.txt  # Python dependencies (Playwright, httpx, dll)
│   ├── Dockerfile        # Image kontainer dengan Chromium + noVNC
│   └── supervisord.conf  # Process manager (bot, Xvfb, VNC, noVNC)
├── docker-compose.yml    # Konfigurasi docker lokal/runner
├── store_knowledge.txt   # Pedoman Toko / SOP CS
├── unanswered_questions.txt # Log pertanyaan sulit yang butuh admin
└── .env                  # Variabel environment (JANGAN DI-COMMIT)
```

---

## 🔄 CI/CD Pipeline
Repositori ini dilengkapi dengan GitHub Actions (`ci-cd.yml`) yang melakukan:
1. **Linting**: Mengecek kode dengan `ruff` pada setiap *push* atau *pull request*.
2. **Deploy Preview**: Membangun image Docker dan merilis environment *preview* lokal saat ada *Pull Request* terbuka.
3. **Deploy Production**: Langsung melakukan pull image terbaru dari Docker Hub dan me-restart container di server utama saat kode di-*merge* ke branch `main`.

---

## ⚠️ Disclaimer
Bot ini dibangun untuk membantu meringankan beban operasional toko (Customer Service). Harap gunakan dengan bijak. Perubahan pada struktur web Shopee (DOM update) mungkin akan memerlukan pembaruan pada *script* Playwright.
