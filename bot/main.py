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
import httpx

from playwright.async_api import async_playwright

# ── Logging & Directory setup ──────────────────────────────────────────────────
LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger(__name__)

# ── Config ─────────────────────────────────────────────────────────────────────
PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/shopee-profile")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/new-webchat/conversations")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

# ── AI Provider Configuration ──────────────────────────────────────────────────
AI_PROVIDER = os.getenv("AI_PROVIDER", "").lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# Auto-detect provider if not explicitly configured but key is present
if not AI_PROVIDER:
    if GEMINI_API_KEY:
        AI_PROVIDER = "gemini"
    else:
        AI_PROVIDER = "ollama"

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


GET_CHAT_ITEMS_JS = r"""() => {
    const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"]');
    if (cells.length > 0) {
        return Array.from(cells);
    }
    const allDivs = document.querySelectorAll('div');
    const items = [];
    for (const div of allDivs) {
        const text = div.textContent || '';
        const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || 
                             text.includes('Yesterday') || 
                             text.includes('Kemarin') ||
                             /\b\d{1,2}[/-]\d{1,2}\b/.test(text);
        const isNotOrder = !text.toLowerCase().includes('total pesanan') && !text.toLowerCase().includes('kirim sebelum');
        if (hasTimestamp && text.length < 300 && isNotOrder) {
            const rect = div.getBoundingClientRect();
            if (rect.height > 40 && rect.height < 120 && rect.width > 100) {
                if (rect.top < window.innerHeight && rect.bottom > 0 && rect.left < window.innerWidth * 0.4) {
                    items.push(div);
                }
            }
        }
    }
    return items.filter(item => !items.some(other => other !== item && item.contains(other)));
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


# ── Bot logic ────────────────────────────────────────
def get_auto_reply(message: str) -> str:
    """Fallback when AI fails or times out."""
    return DEFAULT_REPLY


async def get_ai_reply_ollama(buyer_message: str) -> str:
    """Generate reply using Ollama."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            system_prompt = (
                "Anda adalah Customer Service toko. Anda HANYA boleh menjawab berdasarkan Pedoman Toko berikut ini:\n\n"
                f"=== PEDOMAN TOKO ===\n{STORE_KNOWLEDGE}\n====================\n\n"
                "ATURAN SUPER KETAT:\n"
                "1. Jawab HANYA menggunakan informasi dari Pedoman Toko di atas.\n"
                "2. DILARANG KERAS mengarang, menebak, atau meniru format (seperti mengetik T: atau J:).\n"
                "3. Jika pertanyaan pembeli TIDAK ADA jawabannya di Pedoman Toko, Anda WAJIB membalas dengan KATA INI SAJA: TIDAK TAHU\n"
                "4. Jawablah dengan singkat dan ramah.\n\n"
                "CONTOH BENAR JIKA PERTANYAAN ADA DI PEDOMAN:\n"
                "Pembeli: Barang ready?\n"
                "Anda: Semua barang yang variannya bisa di-klik di etalase berarti ready stock kak, silakan diorder..\n\n"
                "CONTOH BENAR JIKA PERTANYAAN TIDAK ADA DI PEDOMAN (Misal: ongkos kirim, asuransi, nota, dll):\n"
                "Pembeli: Ongkir ke Jakarta berapa kak?\n"
                "Anda: TIDAK TAHU"
            )
            resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
                "model": "qwen2",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": buyer_message}
                ],
                "stream": False,
                "options": {
                    "temperature": 0.0,
                    "top_p": 0.1
                }
            })
            if resp.status_code == 200:
                reply = resp.json().get("message", {}).get("content", "").strip()
                if reply:
                    # SAFETY FILTER: Reject if model hallucinates the store knowledge format
                    if "T:" in reply or "J:" in reply or reply.startswith("T:"):
                        log.warning("Ollama hallucinated Q&A format. Forcing TIDAK TAHU.")
                        return "TIDAK TAHU"
                    return reply
            log.warning("Ollama returned status code: %s, message: %s", resp.status_code, resp.text)
    except Exception as e:
        log.warning("Ollama error: %s", e)
    
    return get_auto_reply(buyer_message)  # fallback


async def get_ai_reply_gemini(buyer_message: str) -> str:
    """Generate reply using Google Gemini API."""
    try:
        if not GEMINI_API_KEY:
            log.warning("GEMINI_API_KEY tidak dikonfigurasi, menggunakan auto-reply bawaan.")
            return get_auto_reply(buyer_message)
        
        import google.generativeai as genai
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        # Run in executor to prevent blocking the async event loop
        loop = asyncio.get_running_loop()
        prompt_text = (
            "Anda adalah Customer Service toko. Anda HANYA boleh menjawab berdasarkan Pedoman Toko berikut ini:\n\n"
            f"=== PEDOMAN TOKO ===\n{STORE_KNOWLEDGE}\n====================\n\n"
            "ATURAN SUPER KETAT:\n"
            "1. Jawab HANYA menggunakan informasi dari Pedoman Toko di atas.\n"
            "2. DILARANG KERAS mengarang, menebak, atau menambahkan informasi yang tidak ada di Pedoman Toko.\n"
            "3. Jika pertanyaan pembeli TIDAK ADA jawabannya di Pedoman Toko, Anda WAJIB membalas dengan KATA INI SAJA: TIDAK TAHU\n"
            "4. Jawablah dengan singkat dan ramah.\n\n"
            f"Pertanyaan Pembeli: {buyer_message}\n"
            "Jawaban Anda:"
        )
        response = await loop.run_in_executor(
            None,
            lambda: model.generate_content(
                prompt_text,
                generation_config=genai.types.GenerationConfig(temperature=0.0)
            )
        )
        reply = response.text.strip()
        if reply:
            return reply
    except Exception as e:
        log.warning("Gemini API error: %s", e)
    return get_auto_reply(buyer_message)  # fallback


async def get_ai_reply(buyer_message: str) -> str:
    """Route the request to the active AI provider (gemini or ollama)."""
    if AI_PROVIDER == "gemini":
        log.info("Menggunakan Gemini API untuk membalas...")
        return await get_ai_reply_gemini(buyer_message)
    else:
        log.info("Menggunakan Ollama lokal untuk membalas...")
        return await get_ai_reply_ollama(buyer_message)


def is_assistant_ai_msg(text: str) -> bool:
    """Check if the text indicates it's from Assistant AI or an Auto-Reply."""
    t = text.lower()
    return (
        "[asisten ai" in t or 
        "asisten ai toko" in t or 
        "ai asistent toko" in t or 
        "asistent ai" in t or
        "auto-reply" in t or
        "auto reply" in t or
        "kami akan segera membalas" in t or
        "variant yg bisa di klik" in t
    )



async def handle_unread_chats(page, replied_cache: set) -> int:
    """
    Find unread and Assistant AI chats, and reply to each one.
    Returns the number of chats processed.
    """
    processed = 0
    try:
        # 0. Dismiss "Restore pages?" dialog if present
        try:
            restore_btn = page.locator("button:has-text('Restore'), button:has-text('Pulihkan')").first
            if await restore_btn.is_visible(timeout=1000):
                log.info("Dismissing 'Restore pages?' dialog...")
                await restore_btn.click()
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        try:
            close_btn = page.locator("[aria-label='Close'], button:has-text('×'), .close-button").first
            if await close_btn.is_visible(timeout=1000):
                log.info("Closing restore pages pop-up via close button...")
                await close_btn.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # 1. Ensure top dropdown is set to "Chat Pembeli"
        try:
            trigger_penjual = page.locator("text=Chat Penjual").first
            trigger_pembeli = page.locator("text=Chat Pembeli").first
            
            if await trigger_penjual.is_visible() and not await trigger_pembeli.is_visible():
                log.info("Detected 'Chat Penjual' active. Clicking to switch to 'Chat Pembeli'...")
                await trigger_penjual.click()
                await page.wait_for_timeout(1000)
                option_pembeli = page.locator("text=Chat Pembeli").last
                await option_pembeli.click()
                await page.wait_for_timeout(2000)
        except Exception as e:
            log.warning("Dropdown check/switch failed: %s", e)

        # 2. Click "Semua Chat" tab if visible to ensure we process all chats
        try:
            semua_chat_tab = page.locator("text=Semua Chat").first
            if await semua_chat_tab.is_visible():
                log.info("Clicking 'Semua Chat' tab...")
                await semua_chat_tab.click()
                await page.wait_for_timeout(2000)
        except Exception as e:
            log.warning("Clicking 'Semua Chat' tab failed: %s", e)

        # Ensure chat list section header is expanded (e.g. "Semua Pembeli" or "Belum Dibalas")
        try:
            # Check if there are any chat items visible in the DOM.
            items_found = await page.evaluate(r"""() => {
                const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"]');
                if (cells.length > 0) return true;
                const divs = [...document.querySelectorAll('div')];
                return divs.some(div => {
                    const text = div.textContent || '';
                    const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || 
                                         text.includes('Yesterday') || 
                                         text.includes('Kemarin') ||
                                         /\b\d{1,2}[/-]\d{1,2}\b/.test(text);
                    const isNotOrder = !text.toLowerCase().includes('total pesanan') && !text.toLowerCase().includes('kirim sebelum');
                    if (hasTimestamp && text.length < 300 && isNotOrder) {
                        const rect = div.getBoundingClientRect();
                        return rect.height > 40 && rect.height < 120 && rect.width > 100;
                    }
                    return false;
                });
            }""")
            
            if not items_found:
                semua_pembeli = page.locator("text=Semua Pembeli").first
                if await semua_pembeli.is_visible():
                    log.info("No chat items detected. Clicking 'Semua Pembeli' section to expand...")
                    await semua_pembeli.click()
                    await page.wait_for_timeout(2000)
                else:
                    belum_dibalas = page.locator("text=Belum Dibalas").first
                    if await belum_dibalas.is_visible():
                        log.info("No chat items detected. Clicking 'Belum Dibalas' section to expand...")
                        await belum_dibalas.click()
                        await page.wait_for_timeout(2000)
        except Exception as e:
            log.warning("Expanding sections failed: %s", e)

        # Debug DOM and Frames (only when DEBUG environment variable is set)
        if os.getenv("DEBUG"):
            try:
                log.info("Page frames count: %d", len(page.frames))
                debug_target = os.getenv("DEBUG_TARGET", "")
                for i, f in enumerate(page.frames):
                    log.info("Frame #%d URL: %s, Name: %s", i, f.url, f.name)
                    if debug_target:
                        try:
                            debug_info = await f.evaluate(r"""(target) => {
                                const results = [];
                                const elements = [...document.querySelectorAll('*')].filter(e => e.textContent.includes(target));
                                for (const el of elements) {
                                    if (el.childNodes.length === 1 || el.classList.length > 0) {
                                        let current = el;
                                        let path = [];
                                        while (current && path.length < 5) {
                                            path.push(current.tagName + '.' + [...current.classList].join('.'));
                                            current = current.parentElement;
                                        }
                                        results.push(path.join(' < '));
                                    }
                                }
                                return results.slice(0, 5);
                            }""", debug_target)
                            if debug_info:
                                log.info("Frame #%d DOM DEBUG (matching '%s'): %s", i, debug_target, debug_info)
                        except Exception as fe:
                            log.warning("Frame #%d eval failed: %s", i, fe)
            except Exception as e:
                log.warning("DOM Debug failed: %s", e)

            # Take a screenshot for visual debugging
            try:
                screenshot_path = os.path.join(LOG_DIR, "screenshot.png")
                await page.screenshot(path=screenshot_path)
                log.info("Saved debug screenshot to: %s", screenshot_path)
            except Exception as ss_err:
                log.warning("Failed to save debug screenshot: %s", ss_err)

        visited_usernames = set()
        max_attempts = 30
        for attempt in range(max_attempts):
            try:
                index = -1
                username = "Unknown"
                
                elements_handle = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
                num_items = await page.evaluate("arr => arr.length", elements_handle)
                
                target_item = None
                target_username = None
                target_index = -1
                for i in range(num_items):
                    item_handle = await page.evaluate_handle(f"(arr) => arr[{i}]", elements_handle)
                    item = item_handle.as_element()
                    if item:
                        text = await item.inner_text()
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        if lines:
                            u_name = lines[0]
                            if u_name not in visited_usernames:
                                target_item = item
                                target_username = u_name
                                target_index = i
                                break
                                
                if not target_item:
                    break
                    
                visited_usernames.add(target_username)
                
                item = target_item
                username = target_username
                index = target_index
                item_text = await item.inner_text()
                
                # Check for unread badge or indicator
                has_unread = await item.query_selector(
                    ".unread-badge, .unread-count, [class*='unread']"
                )
                
                # Check if preview text contains AI indicator
                has_ai = is_assistant_ai_msg(item_text)
                
                # Smart Skip: Skip clicking if it's already replied by the seller (us) 
                # and doesn't contain an unread indicator or AI response.
                if not has_unread and not has_ai:
                    preview_lower = item_text.lower()
                    if (
                        "saya:" in preview_lower or 
                        "anda:" in preview_lower or 
                        "you:" in preview_lower or
                        any(reply.lower()[:15] in preview_lower for reply in AUTO_REPLIES.values()) or
                        DEFAULT_REPLY.lower()[:15] in preview_lower or
                        "gambar" in preview_lower or
                        "image" in preview_lower
                    ):
                        log.info("Skipping chat #%d: already replied by seller", index + 1)
                        continue

                log.info("Processing chat #%d: %s", index + 1, item_text.replace('\n', ' | ')[:80])
                # Use Playwright native click to ensure React events fire correctly
                try:
                    await item.click(timeout=3000)
                except Exception:
                    await item.evaluate("node => node.click()")
                await page.wait_for_timeout(2000)

                # Close any blocking popup/modal
                try:
                    popup_close_selectors = [
                        "button.shopee-popup__close-btn",
                        ".shopee-modal__close",
                        "[class*='popup'] button",
                        "[class*='modal'] button",
                        ".icon-close"
                    ]
                    for sel in popup_close_selectors:
                        try:
                            popup_close = page.locator(sel).first
                            if await popup_close.is_visible():
                                log.info("Closing popup using selector '%s'...", sel)
                                await popup_close.click()
                                await page.wait_for_timeout(1000)
                                break
                        except Exception:
                            pass
                except Exception as e:
                    log.warning("Popup closing attempt failed: %s", e)

                # Click "Lihat History Chat" or "Lihat Pesan Sebelumnya" button
                try:
                    history_btn_selectors = [
                        "text=Lihat History Chat",
                        "text=Lihat Pesan Sebelumnya",
                        "button:has-text('History')",
                        "button:has-text('Sebelumnya')"
                    ]
                    for sel in history_btn_selectors:
                        try:
                            history_btn = page.locator(sel).first
                            if await history_btn.is_visible():
                                log.info("Clicking '%s' button to load chat history...", sel)
                                await history_btn.click()
                                await page.wait_for_timeout(2000)
                                break
                        except Exception:
                            pass
                except Exception as e:
                    log.warning("Loading chat history button click failed: %s", e)


                # Extract chat history from the middle panel using JS
                chat_history = await page.evaluate(r"""() => {
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
                    
                    if (!bestContainer) {
                        bestContainer = document.body;
                    }
                    
                    const bubbles = bestContainer.querySelectorAll('[data-cy="webchat-message-receive"], [data-cy="webchat-message-send"], [class*="message-bubble"], [class*="message_bubble"], [class*="message-item"], [class*="message-row"], [class*="msg-item"], .message, .bubble');
                    
                    const history = [];
                    for (const b of bubbles) {
                        const text = (b.textContent || '').trim();
                        if (!text) continue;
                        
                        let isSeller = false;
                        let current = b;
                        for (let depth = 0; depth < 4; depth++) {
                            if (!current) break;
                            const curStyle = window.getComputedStyle(current);
                            const curClass = current.className || '';
                            const dataCy = current.getAttribute('data-cy') || '';
                            if (
                                dataCy === 'webchat-message-send' ||
                                curClass.includes('seller') || 
                                curClass.split(/[\s-_]/).includes('me') || 
                                curClass.includes('right') || 
                                curClass.includes('send') ||
                                curStyle.justifyContent === 'flex-end' ||
                                curStyle.alignItems === 'flex-end' ||
                                curStyle.alignSelf === 'flex-end' ||
                                curStyle.float === 'right' ||
                                current.getAttribute('style')?.includes('right')
                            ) {
                                isSeller = true;
                                break;
                            }
                            if (dataCy === 'webchat-message-receive') {
                                isSeller = false;
                                break;
                            }
                            current = current.parentElement;
                        }
                        
                        // Position-based check: if bubble center is on the right side of the container or aligns right, it's seller
                        if (!isSeller && bestContainer) {
                            const containerRect = bestContainer.getBoundingClientRect();
                            const bubbleRect = b.getBoundingClientRect();
                            if (containerRect.width > 0) {
                                const relativeLeft = (bubbleRect.left - containerRect.left) / containerRect.width;
                                const relativeRight = (containerRect.right - bubbleRect.right) / containerRect.width;
                                const bubbleCenter = bubbleRect.left + (bubbleRect.width / 2);
                                const containerCenter = containerRect.left + (containerRect.width / 2);
                                
                                if (relativeLeft > 0.4 || (relativeRight < 0.1 && relativeLeft > 0.1) || bubbleCenter > containerCenter + 20) {
                                    isSeller = true;
                                } else if (bubbleCenter < containerCenter - 20) {
                                    isSeller = false;
                                }
                            }
                        }
                        
                        // Color-based check: seller bubbles often have orange/brand-colored backgrounds
                        if (!isSeller) {
                            const bubbleStyle = window.getComputedStyle(b);
                            const bgColor = bubbleStyle.backgroundColor;
                            if (bgColor && (
                                bgColor.includes('238') ||
                                bgColor.includes('255, 87') ||
                                bgColor.includes('ee4d2d') ||
                                b.closest('[class*="seller"]') ||
                                b.closest('[class*="right"]') ||
                                b.closest('[class*="send"]')
                            )) {
                                isSeller = true;
                            }
                        }
                        
                        history.push({
                            text: text,
                            isSeller: isSeller
                        });
                    }
                    
                    const cleanHistory = [];
                    for (const item of history) {
                        if (cleanHistory.length > 0) {
                            const last = cleanHistory[cleanHistory.length - 1];
                            if (last.text.includes(item.text) && last.isSeller === item.isSeller) {
                                continue;
                            }
                            if (item.text.includes(last.text) && last.isSeller === item.isSeller) {
                                cleanHistory[cleanHistory.length - 1] = item;
                                continue;
                            }
                        }
                        cleanHistory.push(item);
                    }
                    
                    return cleanHistory;
                }""")

                # Fallback to selector-based extraction if JS history returns empty
                if not chat_history:
                    log.warning("JS history extraction returned empty, trying fallback message selector...")
                    message_selector = (
                        "[data-cy^='webchat-message'], "
                        ".message-bubble, "
                        "[class*='message-bubble'], "
                        "[class*='message-row'], "
                        "[class*='message-item']"
                    )
                    messages = await page.query_selector_all(message_selector)
                    for msg in messages:
                        msg_text = await msg.inner_text()
                        msg_class = await msg.get_attribute("class") or ""
                        msg_style = await msg.get_attribute("style") or ""
                        msg_data_cy = await msg.get_attribute("data-cy") or ""
                        
                        msg_classes = set(re.split(r'[\s\-_]', msg_class.lower()))
                        is_seller = (
                            msg_data_cy == "webchat-message-send" or
                            "seller" in msg_classes or 
                            "me" in msg_classes or 
                            "right" in msg_classes or 
                            "right" in msg_style.lower()
                        )
                        if not is_seller:
                            parent = await msg.query_selector("xpath=..")
                            if parent:
                                parent_class = await parent.get_attribute("class") or ""
                                parent_classes = set(re.split(r'[\s\-_]', parent_class.lower()))
                                if "seller" in parent_classes or "me" in parent_classes or "right" in parent_classes:
                                    is_seller = True
                        
                        if not is_seller:
                            try:
                                bbox = await msg.bounding_box()
                                if bbox:
                                    container = await page.query_selector("[class*='chat-content'], [class*='conversation']")
                                    if container:
                                        c_bbox = await container.bounding_box()
                                        if c_bbox and c_bbox['width'] > 0:
                                            relative_left = (bbox['x'] - c_bbox['x']) / c_bbox['width']
                                            relative_right = (c_bbox['x'] + c_bbox['width'] - (bbox['x'] + bbox['width'])) / c_bbox['width']
                                            bubble_center = bbox['x'] + (bbox['width'] / 2)
                                            container_center = c_bbox['x'] + (c_bbox['width'] / 2)
                                            
                                            if relative_left > 0.4 or (relative_right < 0.1 and relative_left > 0.1) or bubble_center > container_center + 20:
                                                is_seller = True
                                            elif bubble_center < container_center - 20:
                                                is_seller = False
                            except Exception:
                                pass
                        chat_history.append({
                            "text": msg_text,
                            "isSeller": is_seller
                        })

                if not chat_history:
                    log.info("No message history found, saving DOM and screenshot.")
                    try:
                        await page.screenshot(path=os.path.join(LOG_DIR, "empty_history.png"))
                        dom = await page.evaluate("document.body.innerHTML")
                        with open(os.path.join(LOG_DIR, "dom_dump.html"), "w", encoding="utf-8") as f:
                            f.write(dom)
                    except Exception as e:
                        log.error("Failed to dump DOM: %s", e)
                    log.info("No message history found, skipping.")
                    continue

                # Force isSeller validation (Bug 6)
                for msg in chat_history:
                    msg_lower = msg["text"].lower()
                    if any(reply.lower() in msg_lower for reply in AUTO_REPLIES.values()):
                        msg["isSeller"] = True
                    if DEFAULT_REPLY.lower()[:30] in msg_lower:
                        msg["isSeller"] = True

                # Limit chat history to the last 4 messages to save tokens/RAM
                chat_history = chat_history[-4:]

                last_msg = chat_history[-1]
                last_msg_text = last_msg["text"]
                last_msg_is_seller = last_msg["isSeller"]
                
                is_assistant_ai = is_assistant_ai_msg(last_msg_text)
                
                # Check if the last message is an image
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

                # If the last message is from the seller AND it's not Assistant AI AND it's not an image, skip
                if last_msg_is_seller and not is_assistant_ai and not is_image:
                    log.info("Seller already replied to the latest message. Skipping.")
                    continue

                # Extract the latest buyer message to generate the reply from
                buyer_message = ""
                for msg in reversed(chat_history):
                    if not msg["isSeller"] and not is_assistant_ai_msg(msg["text"]):
                        buyer_message = msg["text"]
                        break
                
                if not buyer_message:
                    # Fallback to the last message if no buyer message was identified
                    buyer_message = last_msg_text

                # Append context if the last message is an image
                if is_image:
                    if buyer_message and buyer_message != last_msg_text:
                        buyer_message = f'[Pesan terakhir berupa gambar. Pesan pembeli sebelumnya: "{buyer_message}"]'
                    else:
                        buyer_message = "[Pesan terakhir berupa gambar]"

                # Bug 1: Double reply prevention
                cache_key = f"{username}:{buyer_message[:50]}"
                if cache_key in replied_cache:
                    log.info("Already replied to '%s' with this message context, skipping.", username)
                    continue

                log.info("Buyer message context: %s", buyer_message[:100])
                reply_text = await get_ai_reply(buyer_message)
                
                if "TIDAK TAHU" in reply_text or reply_text == DEFAULT_REPLY:
                    log.warning("👉 UNANSWERED BUYER MESSAGE (Dicatat ke unanswered_questions.txt): %s", buyer_message)
                    try:
                        unanswered_path = os.path.join(os.path.dirname(KNOWLEDGE_PATH), "unanswered_questions.txt")
                        with open(unanswered_path, "a", encoding="utf-8") as f:
                            f.write(f"\n\nT: {buyer_message}\nJ: \n")
                        log.info("Berhasil mencatat pertanyaan ke %s", unanswered_path)
                    except Exception as e:
                        log.error("Gagal mencatat pertanyaan unanswered: %s", e)
                    
                    # Ubah balasan menjadi teks default alih-alih diam (silent)
                    reply_text = DEFAULT_REPLY

                # 7. Type and send reply
                log.info("=== REPLY ATTEMPT for user '%s' ===", username)
                log.info("Reply text: %s", reply_text[:80])

                # Find input box — prioritize contenteditable in chat area
                input_box = None
                input_sel_used = "none"

                # Strategy 1: Specific contenteditable selectors in chat panel
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

                # Strategy 2: Fallback — find all contenteditable, pick bottommost
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

                # Strategy 3: Last resort — textarea
                if not input_box:
                    input_box = await page.query_selector("textarea")
                    if input_box:
                        input_sel_used = "textarea-fallback"

                if not input_box:
                    log.warning("Could not find chat input box — Shopee may have changed its DOM.")
                    try:
                        fail_path = os.path.join(LOG_DIR, f"no_input_{username}_{int(time.time())}.png")
                        await page.screenshot(path=fail_path)
                        log.warning("Saved no-input screenshot: %s", fail_path)
                    except Exception:
                        pass
                    continue

                log.info("Found input box via: %s", input_sel_used)

                # Click and focus the input box
                await input_box.click()
                await page.wait_for_timeout(300)

                # Detect element type to choose the right input method
                tag_name = await input_box.evaluate("el => el.tagName.toLowerCase()")
                log.info("Input box tag: %s, visible: %s", tag_name, await input_box.is_visible())

                if tag_name in ("input", "textarea"):
                    # Standard input — fill() works
                    await input_box.fill(reply_text)
                else:
                    # contenteditable div — must use keyboard.type()
                    await input_box.evaluate("el => { el.textContent = ''; el.focus(); }")
                    await page.wait_for_timeout(200)
                    await page.keyboard.type(reply_text, delay=30)

                await page.wait_for_timeout(300)

                # Send: try Enter key first
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1000)

                # Verify if the message was sent (input box should be empty)
                input_text_after = await input_box.evaluate(
                    "el => (el.value || el.textContent || '').trim()"
                )

                if input_text_after:
                    # Enter didn't work — try clicking the Send/Kirim button
                    log.info("Enter didn't send message (input still has text), trying Send button...")
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
                        else:
                            log.warning("Send button not visible either")
                    except Exception as send_err:
                        log.warning("Send button click failed: %s", send_err)

                    # Take a failure screenshot for debugging
                    try:
                        fail_path = os.path.join(LOG_DIR, f"send_fail_{username}_{int(time.time())}.png")
                        await page.screenshot(path=fail_path)
                        log.warning("Saved send-failure screenshot: %s", fail_path)
                    except Exception:
                        pass
                else:
                    log.info("=== REPLY RESULT: SUCCESS (sent via Enter) ===")

                log.info("Replied to '%s': %s", username, reply_text[:80])
                replied_cache.add(cache_key)
                processed += 1

            except Exception as exc:
                log.error("Error processing chat item #%d: %s", index + 1, exc)

    except Exception as exc:
        log.error("Error fetching chat list: %s", exc)

    return processed



async def run_bot():
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

    async with async_playwright() as p:
        log.info("Launching persistent Chromium context…")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=os.getenv("HEADLESS", "true").lower() == "true",
            ignore_default_args=["--enable-automation"],
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ],
            viewport={"width": 1280, "height": 900},
        )

        # Anti-bot stealth: override navigator.webdriver
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => false });
        """)

        page = context.pages[0] if context.pages else await context.new_page()

        replied_cache = set()

        log.info("Navigating to Shopee Seller Chat…")
        await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

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
                return

        log.info("Logged in — entering polling loop (every %ds)", POLL_INTERVAL_SECONDS)

        while not shutdown_event.is_set():
            try:
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

                # Scan and reply to unread chats directly on the live page
                count = await handle_unread_chats(page, replied_cache)
                if count:
                    log.info("Processed %d chat(s) this cycle", count)
                else:
                    log.debug("No unread chats")

            except Exception as exc:
                log.error("Unexpected error in poll loop: %s", exc, exc_info=True)
                # Try reloading the page on error to recover state
                try:
                    log.info("Attempting page reload to recover...")
                    await page.reload(wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)
                except Exception as reload_exc:
                    log.error("Failed to reload page: %s", reload_exc)
                try:
                    await asyncio.wait_for(shutdown_event.wait(), timeout=15)
                except asyncio.TimeoutError:
                    pass
                continue

            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=POLL_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                pass

        log.info("Gracefully closing Playwright context and browser...")
        await context.close()
        log.info("Graceful shutdown complete.")


if __name__ == "__main__":
    asyncio.run(run_bot())
