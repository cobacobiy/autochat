"""
Shopee Auto-Reply Bot
Runs as a daemon using Playwright persistent context for session persistence.
"""

import asyncio
import logging
import os
import re
import signal
import sys
import time
import json
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
import httpx

from playwright.async_api import async_playwright, Page
# ── Logging & Directory setup ──────────────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

handlers = [logging.StreamHandler(sys.stdout)]
if "SUPERVISOR_PROCESS_NAME" not in os.environ:
    handlers.append(logging.FileHandler(LOG_FILE))

if LOG_FORMAT == "json":
    import json as json_lib
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            return json_lib.dumps({
                "ts": self.formatTime(record),
                "level": record.levelname,
                "msg": record.getMessage()
            })
    formatter = JsonFormatter()
    for h in handlers:
        h.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/shopee-profile")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/new-webchat/conversations")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))

AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").lower()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

# ── Safety / Limit Configuration ──────────────────────────────────────────────
UNANSWERED_PATH = os.getenv("UNANSWERED_PATH", "/app/unanswered_questions.txt")
MAX_DAILY_REPLIES = int(os.getenv("MAX_DAILY_REPLIES", "500"))
MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", "1000"))

DAILY_REPLY_DATE = ""
DAILY_REPLY_COUNTER = 0
DAILY_SKIP_COUNT = 0
DAILY_UNANSWERED_COUNT = 0
DAILY_AI_REPLIED_COUNT = 0

def cleanup_old_screenshots(log_dir, hours=24):
    try:
        now = time.time()
        for f in os.listdir(log_dir):
            if f.endswith('.png'):
                filepath = os.path.join(log_dir, f)
                if os.stat(filepath).st_mtime < now - hours * 3600:
                    os.remove(filepath)
    except Exception as e:
        log.warning("Failed to clean up screenshots: %s", e)

# ── HTTP Server Health Check ──────────────────────────────────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = {
            "status": "ok",
            "daily_replies": DAILY_REPLY_COUNTER,
            "daily_skips": DAILY_SKIP_COUNT,
            "daily_unanswered": DAILY_UNANSWERED_COUNT,
            "daily_ai_replied": DAILY_AI_REPLIED_COUNT,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())
    def log_message(self, format, *args): pass

threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8080), HealthHandler).serve_forever(), daemon=True).start()

# ── Knowledge Base / RAG Configuration ─────────────────────────────────────────
KNOWLEDGE_PATH = os.getenv("KNOWLEDGE_PATH", "/app/store_knowledge.txt")
STORE_KNOWLEDGE = ""

# Try different paths for local development convenience
paths_to_try = [
    KNOWLEDGE_PATH,
    "store_knowledge.txt",
    "../store_knowledge.txt",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store_knowledge.txt")
]

for p_path in paths_to_try:
    if os.path.exists(p_path):
        try:
            with open(p_path, "r", encoding="utf-8") as f:
                STORE_KNOWLEDGE = f.read().strip()
            log.info("Berhasil membaca store_knowledge dari: %s", p_path)
            KNOWLEDGE_PATH = p_path
            break
        except Exception as e:
            log.error("Gagal membaca %s: %s", p_path, e)

if not STORE_KNOWLEDGE:
    STORE_KNOWLEDGE = "Jawab pertanyaan pembeli dengan singkat, ramah, dan natural."
    log.info("Store knowledge kosong/tidak ditemukan, menggunakan prompt default.")

STORE_KNOWLEDGE_ANSWERS = []

def parse_knowledge_answers():
    global STORE_KNOWLEDGE_ANSWERS
    STORE_KNOWLEDGE_ANSWERS = []
    for line in STORE_KNOWLEDGE.split('\n'):
        if line.startswith('J:'):
            STORE_KNOWLEDGE_ANSWERS.append(line[2:].strip())

parse_knowledge_answers()

def reload_knowledge():
    """Reload STORE_KNOWLEDGE if the file content changes."""
    global STORE_KNOWLEDGE, KNOWLEDGE_PATH
    for p_path in paths_to_try:
        if os.path.exists(p_path):
            try:
                with open(p_path, "r", encoding="utf-8") as f:
                    new_content = f.read().strip()
                if new_content and new_content != STORE_KNOWLEDGE:
                    STORE_KNOWLEDGE = new_content
                    KNOWLEDGE_PATH = p_path
                    parse_knowledge_answers()
                    log.info("🔄 Knowledge base reloaded dari: %s", p_path)
                return
            except Exception as e:
                log.error("Gagal reload knowledge: %s", e)


LAST_TOP_USERNAME = None

GET_CHAT_ITEMS_JS = r"""() => {
    // Ambil elemen chat dari daftar sidebar kiri (maksimal 5 teratas)
    const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"], li');
    if (cells.length > 0) {
        return Array.from(cells).slice(0, 5); 
    }
    
    // Fallback
    const allDivs = [...document.querySelectorAll('div')];
    const fallbackCells = [];
    for (const div of allDivs) {
        const text = div.textContent || '';
        const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || text.includes('Yesterday') || text.includes('Kemarin');
        if (hasTimestamp && text.length > 5 && text.length < 300) {
            const rect = div.getBoundingClientRect();
            if (rect.left > 0 && rect.left < window.innerWidth * 0.4 && rect.height > 20 && rect.height < 150) {
                fallbackCells.push(div);
                if (fallbackCells.length >= 5) break;
            }
        }
    }
    return fallbackCells;
}"""

AUTO_REPLIES = {
    "harga": "Harga sudah tertera di halaman produk. Silakan cek ya kak 😊",
    "stok": "Stok masih tersedia, silakan langsung order kak!",
    "ongkir": "Ongkir dihitung otomatis oleh Shopee sesuai lokasi kakak.",
    "cod": "Maaf, belum tersedia COD untuk saat ini.",
    "garansi": "Produk bergaransi 30 hari jika ada kerusakan dari pabrik.",
    "pengiriman": "Penjaringan Jakarta Utara",
    "dari mana": "Penjaringan Jakarta Utara",
}
DEFAULT_REPLY = "Ada yang bisa dibantu?"

SKIP_MESSAGES = {
    "ok", "oke", "baik", "baik kak", "baik ka", "oke kak", "oke ka",
    "siap", "terima kasih", "makasih", "sami sami", "mks", "thx", "ty",
    "ok kak", "ok ka", "sip", "siap kak", "siap ka", "makasih kak", "makasih ka",
    "nuhun", "suwun"
}

# ── Bot logic ────────────────────────────────────────
def get_auto_reply(message: str) -> str:
    """Fallback when AI fails or times out."""
    msg = message.lower()
    for keyword, reply in AUTO_REPLIES.items():
        if keyword in msg:
            return reply
    return "TIDAK TAHU"

def build_system_prompt() -> str:
    return (
        "Anda adalah Asisten Customer Service toko online yang ramah, sopan, dan luwes.\n\n"
        f"=== KNOWLEDGE BASE ===\n{STORE_KNOWLEDGE}\n====================\n\n"
        "Aturan Menjawab:\n"
        "1. Jawab pertanyaan spesifik mengenai produk berdasarkan [KNOWLEDGE BASE].\n"
        "2. Jika pembeli meminta pilih motif/warna, jawab: \"Halo kak! Untuk pilihan motif atau warna, silakan tuliskan di Catatan Pembeli saat checkout ya kak 😊\"\n"
        "3. Jika pembeli meminta dikirim cepat (buru-buru/kapan dikirim), jawab: \"Pesanan kakak akan segera kami proses dan kirimkan sesuai antrean ya kak, mohon ditunggu 😊\"\n"
        "4. Gunakan akal sehat ala CS manusia. Jika ada sapaan atau obrolan santai, balaslah dengan ramah.\n"
        "5. Jika ada pertanyaan spesifik tentang detail teknis yang benar-benar tidak Anda ketahui dan tidak ada di panduan, barulah Anda boleh meminta maaf dan sampaikan bahwa Anda akan meneruskannya ke admin toko. Jangan pernah mengarang spesifikasi atau harga.\n"
        "6. Jawab sesingkat dan se-natural mungkin, tidak perlu kaku."
    )

async def get_ai_reply(buyer_message: str) -> str:
    system_prompt = build_system_prompt()
    
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                if AI_PROVIDER == "gemini":
                    if not GEMINI_API_KEY:
                        log.error("GEMINI_API_KEY is not set!")
                        return "TIDAK TAHU"
                    # Default to gemini-flash-latest as 1.5 is deprecated
                    model_name = GEMINI_MODEL if GEMINI_MODEL and "1.5" not in GEMINI_MODEL else "gemini-flash-latest"
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
                    payload = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"parts": [{"text": buyer_message}]}],
                        "generationConfig": {"temperature": 0.0, "topP": 0.1}
                    }
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        try:
                            reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                            return _clean_ai_reply(reply)
                        except (KeyError, IndexError) as e:
                            log.warning("Unexpected Gemini response format: %s", e)
                    else:
                        log.warning("Gemini attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        
                elif AI_PROVIDER == "claude":
                    if not ANTHROPIC_API_KEY:
                        log.error("ANTHROPIC_API_KEY is not set!")
                        return "TIDAK TAHU"
                    url = "https://api.anthropic.com/v1/messages"
                    headers = {
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }
                    payload = {
                        "model": ANTHROPIC_MODEL,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": buyer_message}],
                        "max_tokens": 512,
                        "temperature": 0.0
                    }
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        try:
                            reply = resp.json()["content"][0]["text"].strip()
                            return _clean_ai_reply(reply)
                        except (KeyError, IndexError) as e:
                            log.warning("Unexpected Claude response format: %s", e)
                    else:
                        log.warning("Claude attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        
                else:
                    # Default fallback to Ollama
                    resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
                        "model": OLLAMA_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": buyer_message}
                        ],
                        "stream": False,
                        "options": {"temperature": 0.0, "top_p": 0.1, "num_predict": 200}
                    })
                    if resp.status_code == 200:
                        reply = resp.json().get("message", {}).get("content", "").strip()
                        if reply:
                            return _clean_ai_reply(reply)
                    else:
                        log.warning("Ollama attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        
        except Exception as e:
            log.warning("%s attempt %d error: %s", AI_PROVIDER.capitalize(), attempt + 1, e)
        
        if attempt < 1:
            await asyncio.sleep(2)
            
    return "TIDAK TAHU"

def _clean_ai_reply(reply: str) -> str:
    reply_lower = reply.lower()
    if reply_lower.startswith("j:"):
        reply = reply[2:].strip()
    elif reply_lower.startswith("j :"):
        reply = reply[3:].strip()
    elif reply_lower.startswith("anda:"):
        reply = reply[5:].strip()
    elif reply_lower.startswith("anda :"):
        reply = reply[6:].strip()
    elif reply_lower.startswith("jawaban:"):
        reply = reply[8:].strip()
        
    if "t:" in reply.lower() and "\nj:" in reply.lower():
        log.warning("AI hallucinated Q&A format. Forcing TIDAK TAHU.")
        return "TIDAK TAHU"
        
    if len(reply) > 400:
        log.warning("AI reply is suspiciously long (%d chars), likely a hallucination loop. Forcing TIDAK TAHU.", len(reply))
        return "TIDAK TAHU"
        
    return reply


def is_assistant_ai_msg(text: str) -> bool:
    """Check if the text indicates it's from Assistant AI or an Auto-Reply."""
    t = text.lower()
    return (
        "[asisten ai" in t or 
        "asisten ai toko" in t or 
        "ai asistent toko" in t or 
        "asistent ai" in t or
        "dikirim oleh asisten ai" in t or
        "dikirim oleh asisten" in t or
        "auto-reply" in t or
        "auto reply" in t or
        "kami akan segera membalas" in t or
        "variant yg bisa di klik" in t
    )


IS_SELLER_JS = r"""
function isSeller(el, container) {
    const dataCy = el.getAttribute('data-cy') || '';
    if (dataCy.includes('send') || dataCy.includes('seller') || dataCy.includes('to-user')) return true;
    if (dataCy === 'webchat-message-receive') return false;
    
    const className = (el.className || '').toLowerCase();
    if (className.includes('send') || className.includes('seller') || 
        className.includes('self') || className.includes('right')) return true;
    
    let current = el;
    for (let depth = 0; depth < 15; depth++) {
        if (!current) break;
        const style = window.getComputedStyle(current);
        if (style.justifyContent === 'flex-end' || style.textAlign === 'right' || style.alignItems === 'flex-end' || style.flexDirection === 'row-reverse') return true;
        
        const parentClass = (current.parentElement ? current.parentElement.className : '') || '';
        if (typeof parentClass === 'string') {
            const lowerParentClass = parentClass.toLowerCase();
            if (lowerParentClass.includes('send') || lowerParentClass.includes('seller') || lowerParentClass.includes('self') || lowerParentClass.includes('right')) {
                return true;
            }
        }
        current = current.parentElement;
    }
    
    if (container) {
        const cRect = container.getBoundingClientRect();
        const bRect = el.getBoundingClientRect();
        if (cRect.width > 0) {
            const relLeft = (bRect.left - cRect.left) / cRect.width;
            const relRight = (cRect.right - bRect.right) / cRect.width;
            const bubbleCenter = bRect.left + (bRect.width / 2);
            const containerCenter = cRect.left + (cRect.width / 2);
            
            if (relLeft > 0.4 || (relRight < 0.1 && relLeft > 0.1) || bubbleCenter > containerCenter + 10) return true;
            if (bubbleCenter < containerCenter - 10) return false;
        }
    }
    
    const bubbleStyle = window.getComputedStyle(el);
    const bgColor = bubbleStyle.backgroundColor || '';
    if (bgColor && (
        bgColor.includes('238') ||
        bgColor.includes('255, 87') ||
        bgColor.includes('ee4d2d') ||
        bgColor.includes('232, 245') ||
        bgColor.includes('234, 245') ||
        bgColor.includes('214, 255') ||
        bgColor.includes('204, 255') ||
        el.closest('[class*="seller"]') ||
        el.closest('[class*="right"]') ||
        el.closest('[class*="send"]')
    )) {
        return true;
    }
    
    return false;
}
"""




HAS_SETUP_TABS = False

async def do_human_delay(page, min_ms=2000, max_ms=4500):
    import random
    delay = random.randint(min_ms, max_ms)
    await page.wait_for_timeout(delay)

async def setup_chat_view(page) -> bool:
    global HAS_SETUP_TABS
    """Memastikan tampilan chat siap. Tidak akan menekan tombol jika chat sudah tampil."""
    
    # 1. Handle Error Modals (Klik untuk memuat ulang / Coba Lagi)
    try:
        reload_btn = page.locator("text=Klik untuk memuat ulang").first
        if await reload_btn.is_visible(timeout=1000):
            log.info("Detected 'Klik untuk memuat ulang'. Menunggu jeda manusiawi sebelum reload...")
            await do_human_delay(page, 3000, 7000)
            await reload_btn.click()
            await page.wait_for_timeout(3000)
            HAS_SETUP_TABS = False
            return False
            
        coba_lagi_btn = page.locator("button:has-text('Coba Lagi'), text=Coba Lagi").first
        if await coba_lagi_btn.is_visible(timeout=1000):
            log.info("Detected 'Coba Lagi' error modal. Menunggu jeda manusiawi sebelum reload...")
            await do_human_delay(page, 3000, 7000)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            HAS_SETUP_TABS = False
            return False

        html_content = (await page.content()).lower()
        if "terjadi kesalahan" in html_content and ("coba lagi" in html_content or "memuat halaman" in html_content):
            log.info("Detected 'Coba Lagi' error modal from HTML content. Menunggu jeda manusiawi sebelum reload...")
            await do_human_delay(page, 3000, 7000)
            try:
                await page.reload(wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            await page.wait_for_timeout(5000)
            HAS_SETUP_TABS = False
            return False
    except Exception:
        pass

    # 2. Handle Restore Popup
    try:
        restore_btn = page.locator("button:has-text('Restore'), button:has-text('Pulihkan')").first
        if await restore_btn.is_visible(timeout=1000):
            await restore_btn.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass
        
    try:
        close_btn = page.locator("[aria-label='Close'], button:has-text('×'), .close-button").first
        if await close_btn.is_visible(timeout=1000):
            await close_btn.click()
    except Exception:
        pass

    # 3. Cek jika daftar chat sudah terbuka (mendeteksi kotak chat / waktu)
    try:
        items_found = await page.evaluate(r'''() => {
            const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"], li');
            if (cells.length > 0) return true;
            
            const divs = [...document.querySelectorAll('div')];
            return divs.some(div => {
                const text = div.textContent || '';
                return (/\b\d{2}:\d{2}\b/.test(text) || text.includes('Yesterday') || text.includes('Kemarin')) && text.length > 5 && text.length < 300;
            });
        }''')
        
        # Jika chat sudah tampil DAN tab-tab sudah disetup, kita berhenti di sini.
        if items_found and HAS_SETUP_TABS:
            return True
            
        if not items_found:
            HAS_SETUP_TABS = False
    except Exception:
        pass

    # 4. Jika belum tampil (misal baru login), buka tab Chat Pembeli
    try:
        trigger_penjual = page.locator("text=Chat Penjual").first
        trigger_pembeli = page.locator("text=Chat Pembeli").first
        if await trigger_penjual.is_visible() and not await trigger_pembeli.is_visible():
            log.info("Switching to 'Chat Pembeli'...")
            await do_human_delay(page, 1500, 3000)
            await trigger_penjual.click()
            await do_human_delay(page, 1500, 3000)
            await page.locator("text=Chat Pembeli").last.click()
            await page.wait_for_timeout(2000)
    except Exception:
        pass

    # 5. Pastikan tab Semua Chat dan Semua Pembeli diklik sekali (Hanya jalan jika belum terbuka)
    try:
        semua_chat = page.locator("text=Semua Chat").first
        # Tunggu sampai "Semua Chat" visible agar tidak terlewat setelah reload
        await semua_chat.wait_for(state="visible", timeout=10000)
        
        if not HAS_SETUP_TABS:
            # Berikan jeda yang lebih lama di awal agar sistem keamanan Shopee tidak curiga
            log.info("Menunggu UI termuat penuh (jeda 3-6 detik) sebelum setup tab...")
            await do_human_delay(page, 3000, 6000)
            await semua_chat.click()
            await page.wait_for_timeout(2000)
            
            semua_pembeli = page.locator("text=Semua Pembeli").first
            if await semua_pembeli.is_visible():
                await do_human_delay(page, 1500, 3500)
                await semua_pembeli.click()
                await page.wait_for_timeout(1000)
            HAS_SETUP_TABS = True
    except Exception as e:
        log.warning("Gagal setup tab Semua Chat/Semua Pembeli: %s", e)
        # Jangan set HAS_SETUP_TABS = True agar di iterasi berikutnya dicoba lagi
        return False
        
    return True

async def read_riwayat_chat(page) -> str:
   try:
       history_link_selectors = [
           "text=Lihat Semua Riwayat Chat",
           "a:has-text('Lihat Semua Riwayat Chat')",
           "text=Lihat semua riwayat chat",
           "span:has-text('Lihat Semua Riwayat Chat')",
           "div:has-text('Lihat Semua Riwayat Chat')",
       ]
       history_link = None
       for sel in history_link_selectors:
           try:
               loc = page.locator(sel).first
               if await loc.is_visible():
                   history_link = loc
                   break
           except Exception:
               pass
       
       riwayat_buyer_message = ""
       if history_link:
           log.info("Ditemukan link Riwayat Chat. Membuka popup secara aman...")
           
           # Hapus href dari tag <a> agar browser tidak melakukan refresh/navigasi saat diklik, lalu klik.
           try:
               await history_link.evaluate("""node => {
                   if (node.tagName === 'A') {
                       node.removeAttribute('href');
                       node.removeAttribute('target');
                   }
                   node.click();
               }""")
           except Exception as e:
               log.warning("Gagal mengklik link riwayat: %s", e)
               return ""
           
           await page.wait_for_timeout(1000)
           try:
               await page.wait_for_selector(
                   'div[role="dialog"], [class*="modal"]', 
                   state="visible", 
                   timeout=5000
               )
           except Exception:
               log.warning("Popup Riwayat Chat tidak muncul dalam 5 detik")
           
           extracted_msg = await page.evaluate(r'''() => {
               ''' + IS_SELLER_JS + r'''
               let modal = null;
               const dialogs = document.querySelectorAll('div[role="dialog"], [class*="modal"], [class*="popup"], [class*="dialog"]');
               for (const d of dialogs) {
                   if (d.textContent && (d.textContent.includes("Riwayat Chat") || d.textContent.includes("Riwayat chat") || d.textContent.includes("History Chat"))) {
                       modal = d;
                       break;
                   }
               }
               const container = modal || document.body;
               const bubbles = container.querySelectorAll('[data-cy="webchat-message-receive"], [data-cy="webchat-message-send"], [class*="message-bubble"], [class*="message_bubble"], [class*="message-item"], [class*="message-row"], [class*="msg-item"], .message, .bubble, [class*="message_text"], [class*="message-text"]');
               
               const buyerMessages = [];
               for (const el of bubbles) {
                   const text = (el.textContent || '').trim();
                   if (!text) continue;
                   
                   if (text.includes("Asisten AI Toko") || text.includes("Pesan kakak suda masuk") || text.includes("Hello dear! What would you like to ask?")) {
                       continue;
                   }
                   
                   if (!isSeller(el, container)) {
                       buyerMessages.push(text);
                   }
               }
               if (buyerMessages.length > 0) {
                   return buyerMessages[buyerMessages.length - 1];
               }
               return "";
           }''')
           
           if extracted_msg:
               riwayat_buyer_message = extracted_msg.strip()
               log.info("Ekstrak riwayat chat pembeli berhasil: %s", riwayat_buyer_message[:100])
           
           # Coba tutup popup dengan klik tombol close (x)
           close_selectors = [
               "button[aria-label='Close']", "button[aria-label='Tutup']",
               ".shopee-react-modal__close", ".shopee-react-modal__close-btn",
               ".shopee-popup__close-btn", "[class*='modal'] button[class*='close']",
               "[class*='dialog'] button[class*='close']", "button:has-text('✕')",
               "button:has-text('×')", ".icon-close", "[class*='close']",
           ]
           close_btn = None
           for sel in close_selectors:
               try:
                   loc = page.locator(f"[role='dialog'] {sel}").first
                   if await loc.is_visible():
                       close_btn = loc
                       break
               except Exception:
                   pass
           
           if not close_btn:
               for sel in close_selectors:
                   try:
                       loc = page.locator(sel).first
                       if await loc.is_visible():
                           close_btn = loc
                           break
                   except Exception:
                       pass
                       
           if close_btn:
               log.info("Menutup popup Riwayat Chat...")
               try:
                   await close_btn.click(timeout=2000)
               except Exception:
                   await close_btn.evaluate("node => node.click()")
           else:
               log.warning("Close button popup tidak ditemukan! Mencoba Escape...")
               await page.keyboard.press("Escape")
           
           await page.wait_for_timeout(1000)
           await page.evaluate(r'''() => {
               const dialogs = document.querySelectorAll('div[role="dialog"], [class*="modal"], [class*="popup"], [class*="dialog"]');
               dialogs.forEach(d => {
                   if (d.textContent && (d.textContent.includes("Riwayat Chat") || d.textContent.includes("Riwayat chat") || d.textContent.includes("History Chat"))) {
                       d.remove();
                   }
               });
           }''')

           await page.wait_for_timeout(500)
           still_open = await page.evaluate(r'''() => {
               const dialogs = document.querySelectorAll('div[role="dialog"], [class*="modal"]');
               for (const d of dialogs) {
                   if (d.textContent && d.textContent.includes("Riwayat Chat")) return true;
               }
               return false;
           }''')
           if still_open:
               log.warning("Popup Riwayat Chat masih terbuka! Force close via Escape + DOM removal...")
               await page.keyboard.press("Escape")
               await page.wait_for_timeout(500)
               await page.evaluate(r'''() => {
                   document.querySelectorAll('div[role="dialog"], [class*="modal"], [class*="overlay"], [class*="mask"]').forEach(d => d.remove());
               }''')
               await page.wait_for_timeout(500)
               
       return riwayat_buyer_message
   except Exception as e:
       log.warning("Gagal membaca Riwayat Chat (terjadi crash/error): %s. Merefresh halaman...", e)
       try:
           await page.goto("about:blank", timeout=10000)
       except Exception:
           pass
       try:
           await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
           await page.wait_for_timeout(5000)
       except Exception:
           pass
       return ""

async def extract_chat_history(page) -> list:
    chat_history = await page.evaluate(r'''() => {
        ''' + IS_SELLER_JS + r'''
        const messageContainers = [...document.querySelectorAll('div')].filter(el => {
            const className = el.className || '';
            const style = window.getComputedStyle(el);
            return (style.overflowY === 'scroll' || style.overflowY === 'auto' || className.includes('message') || className.includes('chat-content') || className.includes('conversation'))
                && el.querySelectorAll('[class*="message"], [class*="bubble"]').length > 0;
        });
        
        let bestContainer = null;
        let maxBubbles = 0;
        for (const container of messageContainers) {
            const bubbles = container.querySelectorAll('[class*="message"], [class*="bubble"]');
            if (bubbles.length > maxBubbles) {
                maxBubbles = bubbles.length;
                bestContainer = container;
            }
        }
        
        if (!bestContainer) bestContainer = document.body;
        
        const bubbles = bestContainer.querySelectorAll('[data-cy="webchat-message-receive"], [data-cy="webchat-message-send"], [class*="message-bubble"], [class*="message_bubble"], [class*="message-item"], [class*="message-row"], [class*="msg-item"], .message, .bubble');
        
        const history = [];
        for (const b of bubbles) {
            let text = (b.textContent || '').trim();
            if (!text) continue;
            
            // Jika ini adalah kotak preview riwayat, ambil isinya dan perlakukan sebagai pesan pembeli
            let is_seller_msg = isSeller(b, bestContainer);
            if (text.includes('Lihat Semua Riwayat Chat') || (b.closest && b.closest('[class*="history"], [class*="riwayat"]'))) {
                text = text.replace('Lihat Semua Riwayat Chat', '').replace('Riwayat Chat', '').trim();
                is_seller_msg = false; // Anggap sebagai pesan pembeli agar dijawab
                if (!text) continue;
            }
            
            history.push({ text: text, isSeller: is_seller_msg });
        }
        
        const cleanHistory = [];
        for (const item of history) {
            if (cleanHistory.length > 0) {
                const last = cleanHistory[cleanHistory.length - 1];
                if (last.text.includes(item.text) && last.isSeller === item.isSeller) continue;
                if (item.text.includes(last.text) && last.isSeller === item.isSeller) {
                    cleanHistory[cleanHistory.length - 1] = item;
                    continue;
                }
            }
            cleanHistory.push(item);
        }
        return cleanHistory;
    }''')
    
    if not chat_history:
        log.warning("JS history extraction returned empty, trying fallback message selector...")
        message_selector = "[data-cy^='webchat-message'], .message-bubble, [class*='message-bubble'], [class*='message-row'], [class*='message-item']"
        messages = await page.query_selector_all(message_selector)
        container = await page.query_selector("[class*='chat-content'], [class*='conversation']")
        for msg in messages:
            try:
                msg_text = await msg.inner_text()
                is_seller = await page.evaluate(
                    "([el, c]) => {" + IS_SELLER_JS + " return isSeller(el, c); }",
                    [msg, container]
                )
                chat_history.append({"text": msg_text, "isSeller": bool(is_seller)})
            except Exception as e:
                log.warning("Gagal memproses pesan fallback: %s", e)
    return chat_history

async def send_reply(page, reply_text: str, username: str) -> bool:
    input_box = None
    input_sel_used = "none"

    ce_selectors = [
        "[data-cy='webchat-conversation-detail-input'] [contenteditable='true']",
        "[data-testid='chat-input'] [contenteditable='true']",
        ".chat-input [contenteditable='true']",
        "[class*='chat-input'] [contenteditable='true']",
        "[class*='composer'] [contenteditable='true']",
        "[class*='editor'] [contenteditable='true']",
    ]
    for sel in ce_selectors:
        input_box = await page.query_selector(sel)
        if input_box:
            input_sel_used = sel
            break

    if not input_box:
        all_editable = await page.query_selector_all("[contenteditable='true']")
        if all_editable:
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
                input_sel_used = f"position-fallback (y={best_y})"

    if not input_box:
        input_box = await page.query_selector("textarea")
        if input_box:
            input_sel_used = "textarea-fallback"

    if not input_box:
        try:
            page_content = await page.content()
            if "Chat telah diakhiri otomatis" in page_content or "Asisten AI Toko" in page_content:
                log.info("Chat with '%s' is closed or delegated to Shopee's AI Assistant. Skipping reply.", username)
                return True
        except Exception as ce_err:
            log.warning("Failed to check page content for closed chat: %s", ce_err)
        log.warning("Could not find chat input box — Shopee may have changed its DOM.")
        return False

    log.info("Found input box via: %s", input_sel_used)
    
    try:
        await input_box.click(timeout=5000)
    except Exception as e:
        log.warning("Gagal mengklik kotak input (mungkin terhalang elemen lain / CAPTCHA): %s. Menunggu jeda manusiawi sebelum reload...", e)
        await do_human_delay(page, 3000, 7000)
        return -1

    await page.wait_for_timeout(300)

    tag_name = await input_box.evaluate("el => el.tagName.toLowerCase()")
    if tag_name in ("input", "textarea"):
        await input_box.fill(reply_text)
    else:
        await input_box.evaluate("el => { el.textContent = ''; el.focus(); }")
        await page.wait_for_timeout(200)
        await page.keyboard.type(reply_text, delay=30)

    await page.wait_for_timeout(300)
    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1000)

    input_text_after = await input_box.evaluate("el => (el.value || el.textContent || '').trim()")
    if input_text_after:
        log.info("Enter didn't send message (input still has text), trying Send button...")
        try:
            send_button = page.locator(
                "button:has-text('Kirim'), button:has-text('Send'), [data-testid='send-button'], button.send-btn, button[class*='send'], [class*='send'] button, [class*='composer'] button"
            ).first
            if await send_button.is_visible(timeout=2000):
                await send_button.click()
                await page.wait_for_timeout(800)
                log.info("Sent via Send button click")
            else:
                log.warning("Send button not visible either")
        except Exception as send_err:
            log.warning("Send button click failed: %s", send_err)
    else:
        log.info("=== REPLY RESULT: SUCCESS (sent via Enter) ===")
    return True

async def handle_unread_chats(page: Page, replied_cache: dict) -> int:
    global DAILY_REPLY_COUNTER, DAILY_REPLY_DATE, DAILY_SKIP_COUNT, DAILY_UNANSWERED_COUNT, DAILY_AI_REPLIED_COUNT
    current_date = time.strftime("%Y-%m-%d")
    if DAILY_REPLY_DATE != current_date:
        if DAILY_REPLY_DATE:
            log.info("📊 Daily summary [%s]: replied=%d, skipped=%d, unanswered=%d", 
                     DAILY_REPLY_DATE, DAILY_AI_REPLIED_COUNT, DAILY_SKIP_COUNT, DAILY_UNANSWERED_COUNT)
        DAILY_REPLY_DATE = current_date
        DAILY_REPLY_COUNTER = 0
        DAILY_SKIP_COUNT = 0
        DAILY_UNANSWERED_COUNT = 0
        DAILY_AI_REPLIED_COUNT = 0

    processed = 0
    try:
        setup_success = await setup_chat_view(page)
        if not setup_success:
            return 0

        max_attempts = 30
        for attempt in range(max_attempts):
            try:
                # Cek jika ada popup error Shopee menutupi layar agar tidak stuck
                try:
                    coba_lagi_btn = page.locator("button:has-text('Coba Lagi'), text=Coba Lagi").first
                    if await coba_lagi_btn.is_visible(timeout=1000):
                        log.warning("🚨 Popup 'Coba Lagi' terdeteksi saat mencoba membaca chat! Membatalkan sesi ini untuk force reload...")
                        return -1

                    html_content = (await page.content()).lower()
                    if "terjadi kesalahan" in html_content and ("coba lagi" in html_content or "memuat halaman" in html_content):
                        log.warning("🚨 Popup 'Terjadi Kesalahan' terdeteksi saat mencoba membaca chat! Membatalkan sesi ini untuk force reload...")
                        return -1 # Return -1 to signal main loop to reload
                except Exception:
                    pass
                
                index = -1
                username = "Unknown"
                
                try:
                    elements_handle = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
                except Exception as e:
                    log.warning("Stale element reference, re-fetching: %s", e)
                    await page.wait_for_timeout(1000)
                    try:
                        elements_handle = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
                    except Exception as e2:
                        log.error("Failed to re-fetch chat items: %s", e2)
                        break
                
                target_item = None
                target_username = None
                target_index = -1
                # Cek 1 chat teratas saja (standby di paling atas sesuai permintaan user) agar tidak dicurigai bot
                for idx in range(1):
                    try:
                        item_handle = await page.evaluate_handle(f"(arr) => arr.length > {idx} ? arr[{idx}] : null", elements_handle)
                        item = item_handle.as_element()
                        if item:
                            text = await item.inner_text()
                            has_unread = await item.query_selector(".unread-badge, .unread-count, [class*='unread']")
                            has_ai = is_assistant_ai_msg(text)
                            
                            # Cek apakah sudah dibalas (berdasarkan teks di preview)
                            preview_lower = text.lower()
                            already_replied = (
                                "saya:" in preview_lower or 
                                "anda:" in preview_lower or 
                                "you:" in preview_lower or
                                any(reply.lower()[:15] in preview_lower for reply in AUTO_REPLIES.values()) or
                                DEFAULT_REPLY.lower()[:15] in preview_lower or
                                "gagal mengirim" in preview_lower or
                                "tunggu balasan" in preview_lower
                            )
                            
                            # Targetkan chat ini jika ada badge unread/AI, ATAU jika belum dibalas.
                            if has_unread or has_ai or not already_replied:
                                lines = [line.strip() for line in text.split('\n') if line.strip()]
                                if lines:
                                    u_name = lines[0]
                                    # Gunakan teks preview sebagai bagian dari cache key agar jika ada pesan baru (preview berubah), bot merespons lagi.
                                    preview_snippet = text.replace('\n', ' ')[:30]
                                    cache_key_preview = f"PREV_{u_name}_{preview_snippet}"
                                    cache_key_daily = f"{u_name}_{datetime.now().strftime('%Y-%m-%d')}"
                                    
                                    # Abaikan jika preview ini sudah diproses, ATAU jika hari ini sudah pernah dibalas
                                    if cache_key_preview not in replied_cache and cache_key_daily not in replied_cache:
                                        target_item = item
                                        target_username = u_name
                                        target_index = idx
                                        # Simpan cache_key_preview agar jika nanti di-skip, tidak loop berulang kali
                                        target_cache_key_preview = cache_key_preview
                                        break
                    except Exception:
                        pass
                        
                if not target_item:
                    # Tidak ada chat baru/unread di 2 daftar teratas, hentikan pengecekan
                    break
                
                # Simpan preview ini ke cache agar tidak diloop berulang kali jika ternyata di-skip
                replied_cache[target_cache_key_preview] = time.time()
                
                item = target_item
                username = target_username
                index = target_index
                item_text = await item.inner_text()
                
                log.info("Processing chat #%d: %s", index + 1, item_text.replace('\n', ' | ')[:80])
                
                import random
                human_delay = random.randint(4000, 8000)
                log.info("Jeda sejenak %d ms layaknya manusia sebelum klik chat agar tidak dicurigai bot...", human_delay)
                await page.wait_for_timeout(human_delay)
                
                try:
                    await page.evaluate(r'''() => {
                        document.querySelectorAll('div[role="dialog"]').forEach(d => {
                            if (d.textContent && d.textContent.includes("Riwayat Chat")) d.remove();
                        });
                    }''')
                except Exception:
                    pass

                try:
                    # Hindari force=True karena itu memicu deteksi bot Shopee (klik tidak wajar).
                    # Kita klik spesifik di bagian teks/avatar agar tidak memicu tombol menu (tiga titik).
                    target = await item.query_selector("span, [class*='name']")
                    if target:
                        await target.click(timeout=3000)
                    else:
                        await item.click(timeout=3000)
                except Exception as e:
                    log.warning("Gagal mengklik chat secara normal: %s. Mencoba fallback...", e)
                    try:
                        # Fallback: coba klik keseluruhan area item (secara natural)
                        await item.click(timeout=2000)
                    except Exception as fallback_err:
                        log.error("Gagal mengklik chat dengan fallback (mungkin terhalang CAPTCHA): %s. Menunggu jeda manusiawi sebelum reload...", fallback_err)
                        await do_human_delay(page, 3000, 7000)
                        return -1
                await page.wait_for_timeout(2000)

                try:
                    popup_close_selectors = ["button.shopee-popup__close-btn", ".shopee-modal__close", "[class*='popup'] button", "[class*='modal'] button", ".icon-close"]
                    for sel in popup_close_selectors:
                        try:
                            popup_close = page.locator(sel).first
                            if await popup_close.is_visible():
                                await popup_close.click()
                                await page.wait_for_timeout(1000)
                                break
                        except Exception:
                            pass
                except Exception:
                    pass

                # Fitur klik Riwayat Chat dinonaktifkan karena rentan terdeteksi sebagai bot.
                # Kita akan murni mengandalkan history chat yang sudah tampil di layar (DOM utama).
                riwayat_buyer_message = ""
                chat_history = await extract_chat_history(page)

                if not chat_history:
                    log.info("No message history found, saving DOM and screenshot.")
                    try:
                        await page.screenshot(path=os.path.join(LOG_DIR, "empty_history.png"))
                    except Exception:
                        pass
                    log.info("No message history found, skipping.")
                    continue

                for msg in chat_history:
                    msg_lower = msg["text"].lower().strip()
                    # Bug 3 Fixed: exact match check for isSeller
                    if msg_lower in [r.lower().strip() for r in AUTO_REPLIES.values()]:
                        msg["isSeller"] = True
                    if DEFAULT_REPLY.lower()[:30] in msg_lower:
                        msg["isSeller"] = True
                    for ans in STORE_KNOWLEDGE_ANSWERS:
                        if len(ans) > 10 and ans.lower()[:30] in msg_lower:
                            msg["isSeller"] = True

                chat_history = chat_history[-4:]
                last_msg = chat_history[-1]
                last_msg_text = last_msg["text"]
                last_msg_is_seller = last_msg["isSeller"]
                
                if "gagal mengirim" in last_msg_text.lower() or "tunggu balasan pembeli" in last_msg_text.lower():
                    log.info("Chat blocked by Shopee (Gagal mengirim chat). Waiting for buyer to reply. Skipping.")
                    continue

                is_assistant_ai = is_assistant_ai_msg(last_msg_text)
                
                is_image = (
                    not last_msg_text.strip() or
                    "[gambar]" in last_msg_text.lower() or
                    "gambar" == last_msg_text.strip().lower() or
                    "[image]" in last_msg_text.lower() or
                    "[foto]" in last_msg_text.lower() or
                    "[photo]" in last_msg_text.lower() or
                    bool(re.match(r'^\d{1,2}:\d{2}$', last_msg_text.strip()))
                )
                
                if last_msg_is_seller and is_image and len(chat_history) >= 2:
                    prev_msg = chat_history[-2]
                    if prev_msg["isSeller"] and is_assistant_ai_msg(prev_msg["text"]):
                        is_assistant_ai = True

                if last_msg_is_seller and not is_assistant_ai and not is_image:
                    log.info("Seller already replied to the latest message. Skipping.")
                    DAILY_SKIP_COUNT += 1
                    continue

                buyer_message = ""
                found_buyer_msg = False
                for msg in reversed(chat_history):
                    if not msg["isSeller"] and not is_assistant_ai_msg(msg["text"]):
                        buyer_message = msg["text"]
                        found_buyer_msg = True
                        break
                
                if not found_buyer_msg and riwayat_buyer_message:
                    buyer_message = riwayat_buyer_message
                    found_buyer_msg = True
                    log.info("Using buyer message from Riwayat Chat: %s", buyer_message[:100])
                
                force_default_reply = False
                if not found_buyer_msg:
                    if is_assistant_ai:
                        log.info("Assistant AI menu detected with no buyer message. Will reply with DEFAULT_REPLY.")
                        buyer_message = "[ASISTEN_AI_MENU]"
                        force_default_reply = True
                    else:
                        log.info("No buyer message found (buyer did not chat anything). Skipping.")
                        continue

                has_real_buyer_message = bool(buyer_message.strip()) and not force_default_reply

                if is_image:
                    if buyer_message and buyer_message != last_msg_text:
                        buyer_message = f'[Pesan terakhir berupa gambar. Pesan pembeli sebelumnya: "{buyer_message}"]'
                    else:
                        buyer_message = "[Pesan terakhir berupa gambar]"

                cache_key = f"{username}:{buyer_message[:50]}"
                if cache_key in replied_cache:
                    log.debug("Already replied to '%s' with this message context, skipping.", username)
                    continue
                
                buyer_msg_lower = buyer_message.strip().lower().rstrip(".,!?~ ")
                if buyer_msg_lower in SKIP_MESSAGES:
                    log.info("Skipping non-question acknowledgment for '%s': %s", username, buyer_message)
                    replied_cache[cache_key] = time.time()
                    DAILY_SKIP_COUNT += 1
                    continue

                if DAILY_REPLY_COUNTER >= MAX_DAILY_REPLIES:
                    log.warning("⚠️ Daily reply limit reached (%d). Skipping reply for '%s'.", MAX_DAILY_REPLIES, username)
                    replied_cache[cache_key] = time.time()
                    continue

                if force_default_reply:
                    reply_text = DEFAULT_REPLY
                else:
                    log.info("Buyer message context: %s", buyer_message[:100])
                    reply_text = await get_ai_reply(buyer_message)
                
                if reply_text == "TIDAK TAHU":
                    log.warning("👉 API Error / Fallback ke TIDAK TAHU: %s", buyer_message)
                    if has_real_buyer_message:
                        try:
                            clean_msg = re.sub(r'\d{1,2}:\d{2}$', '', buyer_message).strip()
                            with open(UNANSWERED_PATH, "a", encoding="utf-8") as f:
                                f.write(f"\n\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] User: {username}\nT: {clean_msg}\nJ: \n")
                        except Exception as e:
                            log.error("Gagal mencatat: %s", e)
                        log.info("SKIP: Fallback TIDAK TAHU, biarkan admin jawab.")
                        replied_cache[cache_key] = time.time()
                        DAILY_UNANSWERED_COUNT += 1
                        continue
                    else:
                        replied_cache[cache_key] = time.time()
                        DAILY_SKIP_COUNT += 1
                        continue

                if "tidak tahu" in reply_text.lower() or "maaf" in reply_text.lower() or (reply_text == DEFAULT_REPLY and not force_default_reply):
                    log.warning("👉 AI mungkin tidak tahu/meminta maaf untuk: %s", buyer_message)
                    
                    if has_real_buyer_message:
                        try:
                            clean_msg = re.sub(r'\d{1,2}:\d{2}$', '', buyer_message).strip()
                            with open(UNANSWERED_PATH, "a", encoding="utf-8") as f:
                                f.write(f"\n\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] User: {username}\nT: {clean_msg}\nJ: {reply_text}\n")
                        except Exception as e:
                            log.error("Gagal mencatat: %s", e)
                        
                        log.info("Dicatat ke unanswered_questions.txt untuk di-review admin, tapi tetap dikirimkan balasan ala CS.")
                        DAILY_UNANSWERED_COUNT += 1

                log.info("=== REPLY ATTEMPT for user '%s' ===", username)
                log.info("Reply text: %s", reply_text[:80])

                reply_status = await send_reply(page, reply_text, username)
                if reply_status == -1:
                    return -1
                elif reply_status:
                    replied_cache[cache_key] = time.time()
                    DAILY_REPLY_COUNTER += 1
                    DAILY_AI_REPLIED_COUNT += 1
                    log.info("Daily reply count: %d/%d", DAILY_REPLY_COUNTER, MAX_DAILY_REPLIES)
                    processed += 1
                
                await page.wait_for_timeout(2000)

            except Exception as exc:
                exc_msg = str(exc).lower()
                if "target closed" in exc_msg or "browser closed" in exc_msg or "context closed" in exc_msg or "connection closed" in exc_msg:
                    raise exc
                log.error("Error processing chat item #%d: %s", index + 1, exc)

    except Exception as exc:
        log.error("Error fetching chat list: %s", exc)
        exc_msg = str(exc).lower()
        if "target closed" in exc_msg or "browser closed" in exc_msg or "context closed" in exc_msg or "connection closed" in exc_msg or "not attached" in exc_msg:
            raise exc

    return processed


async def run_bot():
    global HAS_SETUP_TABS
    """Main daemon loop."""
    log.info("Starting Shopee Auto-Reply Bot")
    log.info("Profile directory: %s", PROFILE_DIR)
    log.info("Poll interval: %ds", POLL_INTERVAL_SECONDS)

    os.makedirs(PROFILE_DIR, exist_ok=True)

    # Clean up stale Chromium lock file if present to prevent launch errors
    lock_file = os.path.join(PROFILE_DIR, "SingletonLock")
    if os.path.islink(lock_file) or os.path.exists(lock_file):
        try:
            log.info("Removing stale Chromium lock file: %s", lock_file)
            os.unlink(lock_file)
        except Exception as e:
            log.warning("Failed to remove stale lock file: %s", e)

    # Set up graceful shutdown event and signals
    shutdown_event = asyncio.Event()

    def ask_exit(signame):
        log.info("Received signal %s, initiating graceful shutdown...", signame)
        shutdown_event.set()

    loop = asyncio.get_running_loop()
    for signame in ('SIGINT', 'SIGTERM'):
        try:
            loop.add_signal_handler(
                getattr(signal, signame),
                lambda sn=signame: ask_exit(sn)
            )
        except NotImplementedError:
            # Fallback for Windows where loop.add_signal_handler is not implemented
            def win_handler(sig, frame):
                log.info("Received signal %s (Windows), initiating graceful shutdown...", sig)
                loop.call_soon_threadsafe(shutdown_event.set)
            signal.signal(getattr(signal, signame), win_handler)

    replied_cache = {}

    async with async_playwright() as p:
        while not shutdown_event.is_set():
            log.info("Launching persistent Chromium context…")
            try:
                # Clean up stale Chromium lock file again in case browser crashed
                if os.path.islink(lock_file) or os.path.exists(lock_file):
                    try:
                        os.unlink(lock_file)
                    except Exception:
                        pass
                try:
                    context = await asyncio.wait_for(
                        p.chromium.launch_persistent_context(
                            user_data_dir=PROFILE_DIR,
                            headless=os.getenv("HEADLESS", "true").lower() == "true",
                            ignore_default_args=["--enable-automation"],
                            args=[
                                "--no-sandbox",
                                "--disable-setuid-sandbox",
                                "--disable-dev-shm-usage",
                                "--disable-blink-features=AutomationControlled",
                                "--disable-gpu",
                                "--disable-software-rasterizer",
                                "--js-flags=--max-old-space-size=4096",
                                "--hide-crash-restore-bubble",
                            ],
                            viewport={"width": 1280, "height": 900},
                        ),
                        timeout=60.0
                    )
                except Exception as launch_err:
                    log.error("Failed to launch Chromium (timeout or error): %s", launch_err)
                    log.info("Killing dangling Chromium processes to recover...")
                    import subprocess
                    if os.name == 'nt':
                        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"], capture_output=True)
                    else:
                        subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
                        subprocess.run(["pkill", "-f", "chromium"], capture_output=True)
                    
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5)
                    log.error("Restarting script to ensure clean Playwright state...")
                    break
                # Anti-bot stealth: override navigator.webdriver
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                """)

                page = context.pages[0] if context.pages else await context.new_page()

                cycle_count = 0
                last_refresh_time = time.time()
                browser_start_time = time.time()

                log.info("Navigating to Shopee Seller Chat…")
                try:
                    await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(3000)
                except Exception as goto_err:
                    log.error("Gagal memuat halaman utama Shopee (timeout): %s. Restarting browser...", goto_err)
                    try:
                        await context.close()
                    except Exception:
                        pass
                    continue

                # Check if already logged in
                if "login" in page.url or "auth" in page.url:
                    log.warning(
                        "Not logged in! Please log in manually via VNC/headful mode. "
                        "The bot will automatically resume once login is detected. "
                        "Profile will be saved at: %s",
                        PROFILE_DIR,
                    )
                    # Poll page URL to detect when user logs in
                    login_detected = False
                    for _ in range(120): # 120 * 5s = 600s = 10 minutes
                        if shutdown_event.is_set():
                            break
                        await page.wait_for_timeout(5000)
                        # Check if we are logged in now
                        if "login" not in page.url and "auth" not in page.url:
                            log.info("Login detected! Starting polling loop...")
                            login_detected = True
                            await page.wait_for_timeout(3000)
                            break
                    
                    if not login_detected:
                        log.info("Closing persistent Chromium context...")
                        await context.close()
                        break

                log.info("Logged in — entering polling loop (every %ds)", POLL_INTERVAL_SECONDS)

                # Define browser lifetime (e.g. 6 hours = 21600 seconds)
                browser_lifetime_limit = 21600

                while not shutdown_event.is_set():
                    try:
                        # Check if we need to restart the entire browser (e.g., reached lifetime limit)
                        if time.time() - browser_start_time > browser_lifetime_limit:
                            log.info("Browser reached lifetime limit (%d seconds). Scheduling restart...", browser_lifetime_limit)
                            break
    
                        cycle_count += 1
                        
                        # Heartbeat logging every ~5 minutes
                        if cycle_count % 60 == 0:
                            log.info("💓 Bot heartbeat: %d cycles completed, replied_cache size: %d", 
                                     cycle_count, len(replied_cache))
    
                        # Hot reload store_knowledge.txt every ~10 minutes
                        if cycle_count % 120 == 0:
                            reload_knowledge()
                            cleanup_old_screenshots(LOG_DIR, 24)
    
                        # Clean up expired replied_cache items (> 24 hours)
                        now = time.time()
                        expired = [k for k, v in replied_cache.items() if now - v > 86400]
                        for k in expired:
                            del replied_cache[k]
    
                        # Auto-close extra tabs (e.g. captcha popups) and focus main tab
                        if len(context.pages) > 1:
                            log.info("Detected %d open tabs. Closing extra tabs...", len(context.pages))
                            for i in range(len(context.pages) - 1, 0, -1):
                                try:
                                    await context.pages[i].close()
                                except Exception as e:
                                    log.warning("Failed to close extra tab: %s", e)
                            page = context.pages[0]
                            await page.bring_to_front()
                            await page.wait_for_timeout(1000)
    
                        # Cek apakah halaman crash / blank putih (Aw Snap / Out of Memory)
                        try:
                            # Menggunakan textContent agar overlay loading Shopee tidak membuatnya terbaca kosong
                            body_text = await page.evaluate("document.body ? document.body.textContent.trim() : ''")
                            is_blank = len(body_text) < 50  # Halaman tidak merender DOM sama sekali
                            has_crash_text = "Aw, Snap!" in body_text or "Error code:" in body_text or "STATUS_BREAKPOINT" in body_text
                            
                            # Cek popup error UI Shopee ("Terjadi Kesalahan")
                            try:
                                coba_lagi_btn = page.locator("button:has-text('Coba Lagi'), text=Coba Lagi").first
                                if await coba_lagi_btn.is_visible(timeout=1000):
                                    log.warning("🚨 Muncul popup 'Terjadi Kesalahan' dari Shopee. Menandai untuk reload...")
                                    has_crash_text = True
                                else:
                                    html_content = (await page.content()).lower()
                                    if "terjadi kesalahan" in html_content and ("coba lagi" in html_content or "memuat halaman" in html_content):
                                        log.warning("🚨 Muncul popup 'Terjadi Kesalahan' dari Shopee. Menandai untuk reload...")
                                        has_crash_text = True
                            except Exception:
                                pass
                            
                            if (is_blank and "login" not in page.url and "auth" not in page.url) or has_crash_text:
                                log.warning("🚨 TERDETEKSI HALAMAN BLANK PUTIH ATAU CRASH! Menunggu jeda manusiawi sebelum navigasi ulang...")
                                await do_human_delay(page, 3000, 7000)
                                try:
                                    # Navigasi ke beranda seller centre dulu untuk mereset state Shopee Webchat
                                    await page.goto("https://seller.shopee.co.id/", wait_until="domcontentloaded", timeout=30000)
                                    await page.wait_for_timeout(3000)
                                    # Kembali ke halaman chat
                                    await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                                    await page.wait_for_timeout(5000)
                                    HAS_SETUP_TABS = False
                                except Exception as reload_err:
                                    log.error("Gagal saat mencoba force reload halaman blank: %s", reload_err)
                                continue # Lanjut iterasi baru agar tidak mengeksekusi handle_unread_chats di DOM yang kosong
                        except Exception as e:
                            log.warning("Gagal mengecek status halaman blank: %s", e)

                        # Check if main tab is stuck on captcha/error
                        if "captcha" in page.url.lower() or "error" in page.url.lower() or "verify" in page.url.lower():
                            log.warning("Main tab is on captcha/error page (%s). Navigating back to chat...", page.url)
                            await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
    
                        # Dynamic routing check (detect if redirected to login page)
                        if "login" in page.url or "auth" in page.url:
                            log.warning("Detected logout/redirect to login page. Retrying navigation...")
                            await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                            if "login" in page.url or "auth" in page.url:
                                log.error("Still not logged in. Waiting for user login...")
                                try:
                                    await asyncio.wait_for(shutdown_event.wait(), timeout=60)
                                except asyncio.TimeoutError:
                                    pass
                                continue
    
                        # Scheduled page reload every 30 minutes to prevent memory leak / Aw Snap (Error 9)
                        if time.time() - last_refresh_time > 1800:
                            log.info("Performing scheduled page reload to prevent memory leak...")
                            try:
                                await page.goto("about:blank", wait_until="domcontentloaded")
                                await page.wait_for_timeout(1000)
                                await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                                await page.wait_for_timeout(3000)
                                last_refresh_time = time.time()
                                HAS_SETUP_TABS = False
                            except Exception as reload_err:
                                log.error("Scheduled reload failed: %s", reload_err)
    
                        # Scan and reply to unread chats directly on the live page
                        count = await handle_unread_chats(page, replied_cache)
                        if count == -1:
                            log.warning("🔄 Force reload dipicu oleh popup error di tengah pembacaan chat! Menunggu jeda manusiawi...")
                            await do_human_delay(page, 3000, 7000)
                            try:
                                await page.reload(wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(5000)
                                HAS_SETUP_TABS = False
                            except Exception as e:
                                log.error("Gagal reload: %s", e)
                            continue
                            
                        if count:
                            log.info("Processed %d chat(s) this cycle", count)
                        else:
                            log.debug("No unread chats")
    
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
                        except asyncio.TimeoutError:
                            pass
                    except Exception as loop_exc:
                        log.error("Unexpected error in inner poll loop: %s", loop_exc)
                        exc_msg = str(loop_exc).lower()
                        if "target closed" in exc_msg or "browser closed" in exc_msg or "context closed" in exc_msg or "connection closed" in exc_msg or "not attached" in exc_msg or page.is_closed():
                            log.warning("Critical browser crash/closure detected in inner loop. Re-raising...")
                            raise loop_exc
                        
                        try:
                            log.info("Attempting page reload to recover from inner loop error...")
                            await page.goto("about:blank", wait_until="domcontentloaded")
                            await page.wait_for_timeout(1000)
                            await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                        except Exception as reload_exc:
                            log.error("Failed to recover page in inner loop: %s", reload_exc)
                            
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=15)
                        except asyncio.TimeoutError:
                            pass

                log.info("Closing persistent Chromium context...")
                await context.close()

            except Exception as exc:
                log.error("Unexpected error in poll loop: %s", exc, exc_info=True)
                exc_msg = str(exc).lower()
                # If it's a critical browser/context closure, we raise to let the context recreate
                if "target closed" in exc_msg or "browser closed" in exc_msg or "context closed" in exc_msg or "connection closed" in exc_msg or "not attached" in exc_msg or page.is_closed():
                    log.warning("Critical browser crash/closure detected, recreating context...")
                    # Ensure context is closed if possible
                    try:
                        if 'context' in locals():
                            await context.close()
                    except Exception:
                        pass
                    # Jeda sebelum me-restart browser
                    try:
                        await asyncio.wait_for(shutdown_event.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        pass
                    continue
                
                # Try reloading the page on other non-fatal errors to recover state
                try:
                    log.info("Attempting page reload to recover...")
                    try:
                        await page.goto("about:blank", timeout=10000)
                    except Exception:
                        pass
                    await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(3000)
                except Exception as reload_exc:
                    log.error("Failed to reload page: %s", reload_exc)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass

        log.info("Graceful shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Bot dihentikan oleh user (KeyboardInterrupt).")
    except Exception as e:
        log.error("Bot berhenti karena error fatal: %s", e)
