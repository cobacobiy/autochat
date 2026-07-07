import logging
from bot.config import IS_SELLER_JS, SHOPEE_CHAT_URL

log = logging.getLogger(__name__)

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