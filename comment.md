# Catatan Pengembangan Lanjutan (Next Steps)

Syukurlah bot utama sekarang sudah berhasil berjalan dan membalas chat secara otomatis di komputer Anda! Berikut adalah daftar masalah yang tersisa dan rencana perbaikan untuk tahap selanjutnya:

## 1. Masalah Bot Tidak Mau Membalas Chat Lama (Scroll Kebawah)
**Gejala:** 
Di layar Command Prompt, muncul banyak pesan `[WARNING] Could not find chat item for user 'X' anymore`.
**Akar Masalah:**
Shopee Webchat menggunakan sistem *Dynamic Rendering* (Virtual Scroll). Saat bot mengeklik satu chat dan memprosesnya, daftar chat di sebelah kiri seringkali di-reset atau elemen lamanya dihapus oleh Shopee. Akibatnya, saat bot mencari elemen chat urutan ke-2 atau yang ada di bawah, elemen tersebut sudah hilang dari layar.
**Rencana Perbaikan (Selesai):**
- Konsep "Auto-Scroll" dibatalkan karena tidak diperlukan. Bot kini beroperasi dalam **Standby Mode**, yang berarti bot hanya akan membaca dan memproses chat yang benar-benar terlihat di layar (visible) saat itu juga.
- Jika daftar chat me-reset dirinya karena Virtual Scroll Shopee, bot akan kembali ke mode standby dan siap merespons bila ada chat baru masuk atau bila Anda (admin) secara manual men-scroll layar kembali ke bawah.

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

## 3. Instruksi Kerja untuk AI Murah (Panduan Langkah-demi-Langkah)

Jika Anda ingin menggunakan AI lain yang lebih murah/gratis untuk mengubah bot ini agar memakai API cloud (Gemini/Groq) atau membatasi riwayat chat, berikan instruksi/perintah berikut kepadanya:

### Instruksi A: Mengubah Bot untuk Menggunakan Google Gemini API (Gratis/Sangat Murah)
1. **Instal library pendukung:**
   Tambahkan `google-generativeai` ke file requirements atau jalankan:
   `pip install google-generativeai`
2. **Ubah file [main.py](file:///d:/github/autochat/bot/main.py):**
   - Tambahkan import: `import google.generativeai as genai`
   - Definisikan API key di bagian atas config: `GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")`
   - Buat fungsi baru `get_ai_reply_gemini` untuk menggantikan `get_ai_reply_ollama`:
     ```python
     async def get_ai_reply_gemini(buyer_message: str) -> str:
         try:
             if not GEMINI_API_KEY:
                 log.warning("GEMINI_API_KEY tidak dikonfigurasi, menggunakan auto-reply bawaan.")
                 return get_auto_reply(buyer_message)
             
             genai.configure(api_key=GEMINI_API_KEY)
             model = genai.GenerativeModel('gemini-1.5-flash')
             
             # Jalankan di executor agar tidak memblokir event loop async
             loop = asyncio.get_running_loop()
             response = await loop.run_in_executor(
                 None, 
                 lambda: model.generate_content(
                     f"Balas pesan pembeli Shopee ini dengan ramah, singkat, dan alami dalam Bahasa Indonesia. Jika pesan mengandung teks '[Pesan terakhir berupa gambar...]', maka cukup berikan jawaban atas pertanyaan di dalamnya seolah-olah Anda bisa melihat gambarnya: {buyer_message}"
                 )
             )
             reply = response.text.strip()
             if reply:
                 return reply
         except Exception as e:
             log.warning("Gemini API error: %s", e)
         return get_auto_reply(buyer_message) # fallback
     ```
   - Di dalam `handle_unread_chats`, ganti pemanggilan `reply_text = await get_ai_reply_ollama(buyer_message)` dengan `reply_text = await get_ai_reply_gemini(buyer_message)`.

### Instruksi B: Mengubah Bot untuk Menggunakan Groq API (Sangat Murah & Super Cepat)
1. **Instal library pendukung:**
   `pip install groq`
2. **Ubah file [main.py](file:///d:/github/autochat/bot/main.py):**
   - Tambahkan import: `from groq import Groq`
   - Definisikan API key di bagian atas config: `GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")`
   - Buat fungsi baru `get_ai_reply_groq`:
     ```python
     async def get_ai_reply_groq(buyer_message: str) -> str:
         try:
             if not GROQ_API_KEY:
                 log.warning("GROQ_API_KEY tidak dikonfigurasi, menggunakan auto-reply bawaan.")
                 return get_auto_reply(buyer_message)
             
             client = Groq(api_key=GROQ_API_KEY)
             loop = asyncio.get_running_loop()
             
             def call_groq():
                 completion = client.chat.completions.create(
                     model="llama3-8b-8192",
                     messages=[
                         {"role": "system", "content": "Balas pesan pembeli Shopee dengan ramah, singkat, dan alami dalam Bahasa Indonesia. Jika pesan mengandung teks '[Pesan terakhir berupa gambar...]', maka fokuslah membalas pertanyaan pembeli yang ada di dalamnya."},
                         {"role": "user", "content": buyer_message}
                     ],
                     temperature=0.7,
                     max_tokens=150,
                 )
                 return completion.choices[0].message.content
                 
             reply = await loop.run_in_executor(None, call_groq)
             if reply:
                 return reply.strip()
         except Exception as e:
             log.warning("Groq API error: %s", e)
         return get_auto_reply(buyer_message) # fallback
     ```
   - Di dalam `handle_unread_chats`, ganti pemanggilan `reply_text = await get_ai_reply_ollama(buyer_message)` dengan `reply_text = await get_ai_reply_groq(buyer_message)`.

### Instruksi C: Membatasi Riwayat Chat untuk Menghemat Token / RAM
Agar bot tidak mengirim terlalu banyak riwayat chat lama ke API:
1. Buka [main.py](file:///d:/github/autochat/bot/main.py) dan cari bagian ekstraksi `chat_history`.
2. Sebelum history tersebut dievaluasi untuk membalas, batasi array agar hanya menggunakan beberapa pesan terakhir saja.
3. Misalnya, ganti pengiriman history atau parsing dengan memotong array:
   ```python
   # Potong riwayat chat hanya 4 pesan terakhir sebelum menentukan balasan
   chat_history = chat_history[-4:]
   ```

---
*Catatan: Semua kode perbaikan untuk scroll manual, auto-reply bypass, dan skrip run_local.bat sudah diperbarui dan di-push ke repository lokal Anda.*
