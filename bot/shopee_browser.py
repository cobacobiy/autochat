from datetime import datetime
import random
from bot.config import SHOPEE_CHAT_URL, AUTO_REPLIES, DEFAULT_REPLY, LOG_DIR, SKIP_MESSAGES
import logging
import time
import os
import re
from playwright.async_api import Page
from bot.state import bot_state
from bot.utils import do_human_delay, is_assistant_ai_msg
from bot.ai_engine import get_ai_reply
from bot.config import IS_SELLER_JS, GET_CHAT_ITEMS_JS, UNANSWERED_PATH, MAX_DAILY_REPLIES

log = logging.getLogger(__name__)

async def setup_chat_view(page) -> bool:
    """Memastikan tampilan chat siap. Tidak akan menekan tombol jika chat sudah tampil."""
    
    # 1. Handle Error Modals (Klik untuk memuat ulang / Coba Lagi)
    try:
        reload_btn = page.locator("text=Klik untuk memuat ulang").first
        if await reload_btn.is_visible(timeout=1000):
            log.info("Detected 'Klik untuk memuat ulang'. Menunggu jeda manusiawi sebelum reload...")
            await do_human_delay(page, 3000, 7000)
            await reload_btn.click()
            await page.wait_for_timeout(3000)
            bot_state.has_setup_tabs = False
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
            bot_state.has_setup_tabs = False
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
            bot_state.has_setup_tabs = False
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
        if items_found and bot_state.has_setup_tabs:
            return True
            
        if not items_found:
            bot_state.has_setup_tabs = False
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
        
        if not bot_state.has_setup_tabs:
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
            bot_state.has_setup_tabs = True
    except Exception as e:
        log.warning("Gagal setup tab Semua Chat/Semua Pembeli: %s", e)
        # Jangan set bot_state.has_setup_tabs = True agar di iterasi berikutnya dicoba lagi
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


async def handle_unread_chats(page: Page) -> int:
    current_date = time.strftime("%Y-%m-%d")
    if bot_state.daily_reply_date != current_date:
        if bot_state.daily_reply_date:
            log.info("📊 Daily summary [%s]: replied=%d, skipped=%d, unanswered=%d", 
                     bot_state.daily_reply_date, bot_state.daily_ai_replied_count, bot_state.daily_skip_count, bot_state.daily_unanswered_count)
        bot_state.daily_reply_date = current_date
        bot_state.daily_reply_counter = 0
        bot_state.daily_skip_count = 0
        bot_state.daily_unanswered_count = 0
        bot_state.daily_ai_replied_count = 0

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
                # Cek 20 chat teratas, tapi dengan aturan ketat agar tidak dicurigai bot
                for idx in range(20):
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
                            
                            # Targetkan chat ini JIKA:
                            # 1. Chat Asisten AI Toko (bisa di urutan berapapun karena sering nyangkut)
                            # 2. ATAU Chat biasa, TAPI harus di urutan paling atas (standby layaknya manusia)
                            if has_ai or (idx == 0 and (has_unread or not already_replied)):
                                lines = [line.strip() for line in text.split('\n') if line.strip()]
                                if lines:
                                    u_name = lines[0]
                                    # Gunakan teks preview sebagai bagian dari cache key agar jika ada pesan baru (preview berubah), bot merespons lagi.
                                    preview_snippet = text.replace('\n', ' ')[:30]
                                    cache_key_preview = f"PREV_{u_name}_{preview_snippet}"
                                    cache_key_daily = f"{u_name}_{datetime.now().strftime('%Y-%m-%d')}"
                                    
                                    # Abaikan jika preview ini sudah diproses, ATAU jika hari ini sudah pernah dibalas
                                    if cache_key_preview not in bot_state.replied_cache and cache_key_daily not in bot_state.replied_cache:
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
                bot_state.replied_cache[target_cache_key_preview] = time.time()
                
                item = target_item
                username = target_username
                index = target_index
                item_text = await item.inner_text()
                
                log.info("Processing chat #%d: %s", index + 1, item_text.replace('\n', ' | ')[:80])
                
                human_delay = random.randint(4000, 8000)
                log.info("Jeda sejenak %d ms layaknya manusia sebelum klik chat agar tidak dicurigai bot...", human_delay)
                await page.wait_for_timeout(human_delay)
                
                # Re-fetch item to prevent "Element is not attached to the DOM" after delay
                try:
                    elements_handle_fresh = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
                    item_handle_fresh = await page.evaluate_handle(f"(arr) => arr.length > {index} ? arr[{index}] : null", elements_handle_fresh)
                    item_fresh = item_handle_fresh.as_element()
                    if item_fresh:
                        item = item_fresh
                    else:
                        log.warning("Item chat menghilang dari DOM setelah jeda. Skip cycle ini.")
                        continue
                except Exception as e:
                    log.warning("Gagal mengambil ulang elemen chat setelah jeda: %s", e)
                    continue
                
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
                    for ans in bot_state.knowledge_answers.values():
                        if len(ans) > 10 and ans.lower()[:30] in msg_lower:
                            msg["isSeller"] = True
                    if username in bot_state.sent_messages and msg_lower in bot_state.sent_messages[username]:
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
                    bot_state.daily_skip_count += 1
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
                if cache_key in bot_state.replied_cache:
                    log.debug("Already replied to '%s' with this message context, skipping.", username)
                    continue
                
                buyer_msg_lower = buyer_message.strip().lower().rstrip(".,!?~ ")
                if buyer_msg_lower in SKIP_MESSAGES:
                    log.info("Skipping non-question acknowledgment for '%s': %s", username, buyer_message)
                    bot_state.replied_cache[cache_key] = time.time()
                    bot_state.daily_skip_count += 1
                    continue

                if bot_state.daily_reply_counter >= MAX_DAILY_REPLIES:
                    log.warning("⚠️ Daily reply limit reached (%d). Skipping reply for '%s'.", MAX_DAILY_REPLIES, username)
                    bot_state.replied_cache[cache_key] = time.time()
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
                        bot_state.replied_cache[cache_key] = time.time()
                        bot_state.daily_unanswered_count += 1
                        continue
                    else:
                        bot_state.replied_cache[cache_key] = time.time()
                        bot_state.daily_skip_count += 1
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
                        bot_state.daily_unanswered_count += 1

                log.info("=== REPLY ATTEMPT for user '%s' ===", username)
                log.info("Reply text: %s", reply_text[:80])

                reply_status = await send_reply(page, reply_text, username)
                if reply_status == -1:
                    return -1
                elif reply_status:
                    if username not in bot_state.sent_messages:
                        bot_state.sent_messages[username] = set()
                    bot_state.sent_messages[username].add(reply_text.strip().lower())
                    
                    bot_state.replied_cache[cache_key] = time.time()
                    bot_state.daily_reply_counter += 1
                    bot_state.daily_ai_replied_count += 1
                    log.info("Daily reply count: %d/%d", bot_state.daily_reply_counter, MAX_DAILY_REPLIES)
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

