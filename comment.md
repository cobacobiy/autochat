# Catatan Pengembangan Lanjutan (Next Steps)

Syukurlah bot utama sekarang sudah berhasil berjalan dan membalas chat secara otomatis di komputer Anda! Berikut adalah daftar masalah yang tersisa dan rencana perbaikan untuk tahap selanjutnya:

## 1. Masalah Bot Tidak Mau Membalas Chat Lama (Scroll Kebawah)
**Gejala:** 
Di layar Command Prompt, muncul banyak pesan `[WARNING] Could not find chat item for user 'X' anymore`.
**Akar Masalah:**
Shopee Webchat menggunakan sistem *Dynamic Rendering* (Virtual Scroll). Saat bot mengeklik satu chat dan memprosesnya, daftar chat di sebelah kiri seringkali di-reset atau elemen lamanya dihapus oleh Shopee. Akibatnya, saat bot mencari elemen chat urutan ke-2 atau yang ada di bawah, elemen tersebut sudah hilang dari layar.
**Rencana Perbaikan:**
- Kita perlu membuat logika **Auto-Scroll**. Sebelum bot mencari `target_item`, bot harus memaksa layar daftar chat sebelah kiri untuk *scroll down* hingga username yang dicari muncul kembali di layar (menggunakan `element.scrollIntoView()`).
- Jika daftar chat me-reset dirinya, bot harus melakukan pencarian ulang daftar (re-query) dari awal setiap kali selesai membalas 1 chat, bukan menyimpan daftar elemen di awal lalu memakainya secara berurutan.

## 2. Membuat AI Menjadi Lebih Murah (Cost Optimization)
Saat ini bot menggunakan model `phi3:mini` melalui sistem lokal Ollama. Karena Ollama dijalankan di komputer sendiri, sebenarnya penggunaan AI ini sudah **100% Gratis** tanpa biaya API sepeserpun. 

Namun, jika Anda merasa *performa komputer* (RAM/CPU) menjadi berat, atau jika Anda berencana memindahkannya ke server (VPS) dan ingin mencari alternatif AI yang lebih hemat daya, berikut opsinya:

**Opsi A: Menggunakan API Pihak Ketiga yang Sangat Murah / Gratis**
Alih-alih memaksa komputer Anda berpikir keras dengan Ollama, kita bisa menyambungkan bot ke API cloud:
- **Google Gemini Flash (Gratis Limit Harian):** Sangat cerdas dan cepat, dan Google menyediakan *free tier* (gratis) untuk penggunaan moderat.
- **Groq API (Llama 3):** Super cepat dan biayanya nyaris gratis atau sangat murah dibanding OpenAI ChatGPT.

**Opsi B: Memperkecil Prompt & Penggunaan Memori**
- Jika tetap ingin memakai Ollama di komputer, kita bisa membatasi *history* chat yang dikirimkan ke AI hanya maksimal 3 pesan terakhir saja agar AI merespons lebih cepat dan hemat daya CPU.

---
*Catatan: Semua kode perbaikan untuk fitur membaca balasan yang terlewat (Auto-Reply Bypass) dan skrip `run_local.bat` sudah sukses di-push ke repository GitHub Anda.*
