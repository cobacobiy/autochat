# Perbaikan Chat Reply — Langkah-langkah untuk AI

> **Status**: Bot sudah bisa pilih tab "Semua Pembeli" dan klik chat,
> tapi **belum bisa mengirim balasan** sama sekali.

---

## Masalah Utama (Root Cause)

### Bug 1: `fill()` tidak bekerja pada `contenteditable` div
Shopee Seller Chat menggunakan `<div contenteditable="true">` sebagai input box, **BUKAN** `<textarea>`.
Playwright `.fill()` hanya bekerja untuk `<input>` dan `<textarea>`.
Ketika dipanggil pada `contenteditable`, teks tidak masuk dan tidak ada error yang jelas.

**File**: `bot/main.py` sekitar baris 475-489

**Kode bermasalah**:
```python
input_box = await page.query_selector(
    "[data-testid='chat-input'], "
    ".chat-input textarea, "
    "textarea.chat-input__textarea, "
    "textarea, "                              # ← terlalu umum
    ".chat-input [contenteditable='true'], "
    "[class*='chat'] [contenteditable='true']"
)
if not input_box:
    log.warning("Could not find chat input box")
    continue

await input_box.click()
await input_box.fill(reply_text)              # ← GAGAL di contenteditable
await page.keyboard.press("Enter")
```

### Bug 2: Selector input box terlalu luas
Selector `textarea` tanpa scope bisa menangkap element yang salah (misal textarea di bagian lain halaman).

### Bug 3: `isSeller` detection bisa salah
Bubble detection berbasis posisi (kiri/kanan) dan class name bisa salah klasifikasi, menyebabkan bot skip chat yang seharusnya dibalas.

---

## Langkah Perbaikan (Step-by-Step)

### Step 1: Fix input box selector — gunakan contenteditable yang lebih spesifik

Ganti selector input box di `bot/main.py` (sekitar baris 475-482) dengan:

```python
# Cari input box — prioritaskan contenteditable di area chat
input_box = None

# Strategi 1: Cari contenteditable di panel chat (kanan bawah)
selectors = [
    "[data-testid='chat-input'] [contenteditable='true']",
    ".chat-input [contenteditable='true']",
    "[class*='chat-input'] [contenteditable='true']",
    "[class*='composer'] [contenteditable='true']",
    "[class*='editor'] [contenteditable='true']",
]
for sel in selectors:
    input_box = await page.query_selector(sel)
    if input_box:
        log.info("Found input box via selector: %s", sel)
        break

# Strategi 2: Fallback — cari semua contenteditable, pilih yang paling bawah
if not input_box:
    all_editable = await page.query_selector_all("[contenteditable='true']")
    if all_editable:
        # Pilih yang posisinya paling bawah di halaman (biasanya input chat)
        best = None
        best_y = -1
        for el in all_editable:
            try:
                bbox = await el.bounding_box()
                if bbox and bbox['y'] > best_y:
                    best_y = bbox['y']
                    best = el
            except Exception:
                pass
        if best:
            input_box = best
            log.info("Found input box via position-based fallback (y=%s)", best_y)

# Strategi 3: Fallback terakhir — textarea
if not input_box:
    input_box = await page.query_selector("textarea")
    if input_box:
        log.info("Found input box via textarea fallback")

if not input_box:
    log.warning("Could not find chat input box — Shopee may have changed its DOM.")
    continue
```

### Step 2: Fix cara mengetik teks — gunakan `type()` bukan `fill()`

Ganti mekanisme pengisian teks (baris 487-490) dengan:

```python
await input_box.click()
await page.wait_for_timeout(300)

# Cek apakah ini contenteditable atau textarea/input
tag_name = await input_box.evaluate("el => el.tagName.toLowerCase()")

if tag_name in ("input", "textarea"):
    # Untuk input/textarea biasa, fill() berfungsi
    await input_box.fill(reply_text)
else:
    # Untuk contenteditable div, gunakan keyboard.type()
    # Bersihkan dulu isi yang ada
    await input_box.evaluate("el => { el.textContent = ''; el.focus(); }")
    await page.wait_for_timeout(200)
    await page.keyboard.type(reply_text, delay=30)

await page.wait_for_timeout(300)
```

### Step 3: Fix mekanisme kirim — coba Enter, lalu fallback ke tombol Kirim

Ganti bagian kirim pesan (baris 489-506) dengan:

```python
# Kirim pesan: coba Enter dulu
await page.keyboard.press("Enter")
await page.wait_for_timeout(1000)

# Verifikasi apakah pesan sudah terkirim (input box kosong)
input_text_after = await input_box.evaluate(
    "el => (el.value || el.textContent || '').trim()"
)

if input_text_after:
    # Enter tidak berhasil, coba klik tombol Send/Kirim
    log.info("Enter didn't send message, trying Send button...")
    try:
        send_button = page.locator(
            "button:has-text('Kirim'), "
            "button:has-text('Send'), "
            "[data-testid='send-button'], "
            "button.send-btn, "
            "button[class*='send'], "
            "[class*='send'] button, "
            "[class*='composer'] button"
        ).first
        if await send_button.is_visible(timeout=2000):
            await send_button.click()
            await page.wait_for_timeout(800)
            log.info("Sent via Send button click")
    except Exception as send_err:
        log.warning("Send button also failed: %s", send_err)
else:
    log.info("Message sent via Enter key")
```

### Step 4: Tambahkan logging untuk debugging

Tambahkan log sebelum dan sesudah setiap langkah penting agar mudah di-debug:

```python
log.info("=== REPLY ATTEMPT for user '%s' ===", username)
log.info("Reply text: %s", reply_text[:80])
log.info("Input box tag: %s", tag_name)
log.info("Input box visible: %s", await input_box.is_visible())
# ... (setelah kirim)
log.info("=== REPLY RESULT: %s ===", "SUCCESS" if not input_text_after else "FAILED")
```

### Step 5: Perbaiki isSeller detection — tambahkan validasi warna bubble

Shopee biasanya menggunakan warna berbeda untuk bubble seller vs buyer.
Tambahkan pengecekan warna background di JS extraction (`chat_history` evaluation, baris 280-367):

```javascript
// Di dalam loop bubble detection, tambahkan setelah position-based check:
if (!isSeller) {
    const bubbleStyle = window.getComputedStyle(b);
    const bgColor = bubbleStyle.backgroundColor;
    // Shopee seller bubbles biasanya berwarna oranye/merah muda
    // Buyer bubbles biasanya putih/abu-abu
    if (bgColor && (
        bgColor.includes('238') ||  // orange-ish
        bgColor.includes('255, 87') ||
        bgColor.includes('ee4d2d') ||
        b.closest('[class*="seller"]') ||
        b.closest('[class*="right"]') ||
        b.closest('[class*="send"]')
    )) {
        isSeller = true;
    }
}
```

### Step 6: Screenshot saat gagal kirim (untuk debugging)

Tambahkan screenshot setiap kali gagal kirim pesan:

```python
if input_text_after:
    # Pesan belum terkirim — simpan screenshot untuk debugging
    try:
        fail_path = os.path.join(LOG_DIR, f"send_fail_{username}_{int(time.time())}.png")
        await page.screenshot(path=fail_path)
        log.warning("Saved send-failure screenshot: %s", fail_path)
    except Exception:
        pass
```

Jangan lupa `import time` di bagian atas file.

---

## Ringkasan Perubahan File

| File | Perubahan |
|------|-----------|
| `bot/main.py` baris 475-510 | Ganti selector, ganti fill→type, perbaiki send mechanism |
| `bot/main.py` baris 280-367 | Tambah warna-based isSeller detection |
| `bot/main.py` baris 6 | Tambah `import time` |

---

## Opsional: Integrasi AI Murah untuk Auto-Reply

Jika ingin mengganti reply statis (keyword matching) dengan AI yang lebih pintar:

### Opsi 1: Google Gemini Flash (GRATIS)
- Daftar API key di https://aistudio.google.com
- Model: `gemini-2.0-flash` (gratis, 15 RPM / 1500 RPD)
- Install: `pip install google-genai`
- Tambah di `requirements.txt`: `google-genai`

```python
# Di bagian atas main.py
import google.genai as genai

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
gemini_client = None
if GEMINI_API_KEY:
    gemini_client = genai.Client(api_key=GEMINI_API_KEY)

async def get_ai_reply(buyer_message: str) -> str:
    """Generate reply menggunakan Gemini Flash."""
    if not gemini_client:
        return get_auto_reply(buyer_message)  # fallback ke keyword
    
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.0-flash",
            contents=f"""Kamu adalah customer service toko online di Shopee.
Balas pesan pembeli berikut dengan ramah, singkat (1-2 kalimat), dan dalam Bahasa Indonesia.
Jangan tawarkan diskon. Jika tidak tahu jawabannya, katakan tim akan segera membalas.

Pesan pembeli: {buyer_message}"""
        )
        reply = response.text.strip()
        if reply:
            return reply
    except Exception as e:
        log.warning("Gemini API error: %s", e)
    
    return get_auto_reply(buyer_message)  # fallback
```

Lalu ganti `get_auto_reply(buyer_message)` di baris 470 dengan `await get_ai_reply(buyer_message)`.

### Opsi 2: Ollama (100% gratis, lokal)
- Install Ollama: https://ollama.ai
- Pull model kecil: `ollama pull phi3:mini`
- Tidak perlu API key, jalan di lokal

```python
import httpx

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

async def get_ai_reply_ollama(buyer_message: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": "phi3:mini",
                "prompt": f"Balas pesan pembeli Shopee ini dengan ramah dan singkat dalam Bahasa Indonesia: {buyer_message}",
                "stream": False
            })
            return resp.json().get("response", "").strip() or get_auto_reply(buyer_message)
    except Exception as e:
        log.warning("Ollama error: %s", e)
        return get_auto_reply(buyer_message)
```

### Environment Variables yang perlu ditambahkan di `docker-compose.yml`:
```yaml
environment:
  - GEMINI_API_KEY=${GEMINI_API_KEY:-}
  # atau untuk Ollama:
  - OLLAMA_URL=http://host.docker.internal:11434
```

---

## Urutan Prioritas Pengerjaan

1. ✅ **PERTAMA**: Fix reply mechanism (Step 1-3) — ini yang paling kritis
2. ✅ **KEDUA**: Tambah debugging (Step 4, 6) — supaya bisa lihat apa yang gagal
3. ⬜ **KETIGA**: Perbaiki isSeller detection (Step 5) — mengurangi false skip
4. ⬜ **KEEMPAT**: Integrasi AI (opsional) — setelah reply manual berfungsi
