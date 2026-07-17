import logging
import time
import os
import random
import re
from datetime import datetime
from playwright.async_api import Page

from bot.config import (
    AUTO_REPLIES, DEFAULT_REPLY, LOG_DIR,
    SKIP_MESSAGES, GET_CHAT_ITEMS_JS,
    UNANSWERED_PATH, MAX_DAILY_REPLIES
)
from bot.state import bot_state
from bot.utils import do_human_delay, is_assistant_ai_msg
from bot.ai_engine import get_ai_reply
from bot.chat_navigation import setup_chat_view
from bot.chat_parser import extract_chat_history
from bot.chat_sender import send_reply

log = logging.getLogger(__name__)

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
                try:
                    riwayat_loc = page.locator("text='Lihat Semua Riwayat Chat'")
                    if await riwayat_loc.count() > 0:
                        parent_text = await riwayat_loc.first.locator("xpath=..").inner_text()
                        for line in reversed(parent_text.split('\n')):
                            line = line.strip()
                            if line.lower().startswith(username.lower() + ":") or line.lower().startswith(username.lower() + " :"):
                                idx = line.find(":")
                                riwayat_buyer_message = line[idx+1:].strip()
                                break
                except Exception:
                    pass
                    
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

                # Deteksi pesan pembatalan/cancel/refund — serahkan ke admin manusia
                _cancel_keywords = [
                    "cancel", "pembatalan", "batalkan", "batal", "dibatalkan",
                    "mau batal", "minta batal", "tolong batal", "refund",
                    "pengembalian dana", "kembalikan dana", "uang kembali",
                    "retur", "return", "mau cancel",
                ]
                if has_real_buyer_message and any(kw in buyer_msg_lower for kw in _cancel_keywords):
                    log.warning("🚫 Pesan mengandung kata pembatalan/cancel dari '%s': %s. Diserahkan ke admin.", username, buyer_message[:80])
                    try:
                        clean_msg = re.sub(r'\d{1,2}:\d{2}$', '', buyer_message).strip()
                        with open(UNANSWERED_PATH, "a", encoding="utf-8") as f:
                            f.write(f"\n\n[{datetime.now().strftime('%Y-%m-%d %H:%M')}] User: {username}\nT: {clean_msg}\nJ: [PEMBATALAN - Diserahkan ke admin]\n")
                    except Exception as e:
                        log.error("Gagal mencatat pembatalan: %s", e)
                    bot_state.replied_cache[cache_key] = time.time()
                    bot_state.daily_unanswered_count += 1
                    continue

                if force_default_reply:
                    reply_text = DEFAULT_REPLY
                else:
                    log.info("Buyer message context: %s", buyer_message[:100])
                    reply_text = await get_ai_reply(buyer_message)
                if reply_text == "SKIP":
                    log.info("AI memutuskan untuk SKIP pesan ini (mungkin sekadar ucapan terima kasih/konfirmasi).")
                    bot_state.replied_cache[cache_key] = time.time()
                    bot_state.daily_skip_count += 1
                    continue
                
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