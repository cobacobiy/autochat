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

GET_CHAT_ITEMS_JS = r"""() => {
    const allDivs = document.querySelectorAll('div');
    const items = [];
    for (const div of allDivs) {
        const text = div.textContent || '';
        const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || 
                             text.includes('Yesterday') || 
                             text.includes('Kemarin') ||
                             /\b\d{1,2}[/-]\d{1,2}\b/.test(text);
        if (hasTimestamp && text.length < 300) {
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
DEFAULT_REPLY = "Halo kak! Terima kasih sudah menghubungi kami. Tim kami akan segera membalas 😊"


# ── Bot logic ────────────────────────────────────────
def get_auto_reply(message: str) -> str:
    """Match message keywords to canned replies."""
    msg_lower = message.lower()
    for keyword, reply in AUTO_REPLIES.items():
        if keyword in msg_lower:
            return reply
    return DEFAULT_REPLY


async def get_ai_reply_ollama(buyer_message: str) -> str:
    """Generate reply using Ollama."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{OLLAMA_URL}/api/generate", json={
                "model": "phi3:mini",
                "prompt": f"Balas pesan pembeli Shopee ini dengan ramah dan singkat dalam Bahasa Indonesia: {buyer_message}",
                "stream": False
            })
            if resp.status_code == 200:
                reply = resp.json().get("response", "").strip()
                if reply:
                    return reply
            log.warning("Ollama returned status code: %s", resp.status_code)
    except Exception as e:
        log.warning("Ollama error: %s", e)
    
    return get_auto_reply(buyer_message)  # fallback


def is_assistant_ai_msg(text: str) -> bool:
    """Check if the text indicates it's from Assistant AI."""
    t = text.lower()
    return "[asisten ai" in t or "asisten ai toko" in t or "ai asistent toko" in t or "asistent ai" in t



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
                const divs = [...document.querySelectorAll('div')];
                return divs.some(div => {
                    const text = div.textContent || '';
                    const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || 
                                         text.includes('Yesterday') || 
                                         text.includes('Kemarin') ||
                                         /\b\d{1,2}[/-]\d{1,2}\b/.test(text);
                    if (hasTimestamp && text.length < 300) {
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

        # Extract usernames/identifiers for all chat items first to avoid mismatch after clicks
        chat_identifiers = []
        try:
            elements_handle = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
            num_items = await page.evaluate("arr => arr.length", elements_handle)
            for i in range(num_items):
                item_handle = await page.evaluate_handle(f"(arr) => arr[{i}]", elements_handle)
                item = item_handle.as_element()
                if item:
                    text = await item.inner_text()
                    lines = [line.strip() for line in text.split('\n') if line.strip()]
                    if lines:
                        username = lines[0]
                        chat_identifiers.append(username)
            log.info("Found %d chat item(s) in sidebar list: %s", len(chat_identifiers), chat_identifiers)
        except Exception as e:
            log.error("Failed to extract chat items via JS: %s", e)

        # Process chats one by one by finding the element for each username to avoid detachment and mismatch
        for index, username in enumerate(chat_identifiers):
            try:
                elements_handle = await page.evaluate_handle(GET_CHAT_ITEMS_JS)
                fresh_length = await page.evaluate("arr => arr.length", elements_handle)
                
                target_item = None
                for i in range(fresh_length):
                    item_handle = await page.evaluate_handle(f"(arr) => arr[{i}]", elements_handle)
                    item = item_handle.as_element()
                    if item:
                        text = await item.inner_text()
                        lines = [line.strip() for line in text.split('\n') if line.strip()]
                        if lines and lines[0] == username:
                            target_item = item
                            break
                            
                if not target_item:
                    log.warning("Could not find chat item for user '%s' anymore", username)
                    continue
                    
                item = target_item
                item_text = await item.inner_text()
                
                # Check for unread badge or indicator
                has_unread = await item.query_selector(
                    ".unread-badge, .unread-count, [class*='unread']"
                )
                
                # Check if preview text contains AI indicator
                has_ai = "[asisten ai" in item_text.lower()
                
                # Smart Skip: Skip clicking if it's already replied by the seller (us) 
                # and doesn't contain an unread indicator or AI response.
                if not has_unread and not has_ai:
                    preview_lower = item_text.lower()
                    if (
                        "saya:" in preview_lower or 
                        "anda:" in preview_lower or 
                        "you:" in preview_lower or
                        any(reply.lower()[:15] in preview_lower for reply in AUTO_REPLIES.values()) or
                        DEFAULT_REPLY.lower()[:15] in preview_lower
                    ):
                        log.info("Skipping chat #%d: already replied by seller", index + 1)
                        continue

                log.info("Processing chat #%d: %s", index + 1, item_text.replace('\n', ' | ')[:80])
                # await item.scroll_into_view_if_needed()
                await item.evaluate("node => node.click()")
                await page.wait_for_timeout(2000)

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
                    
                    const bubbles = bestContainer.querySelectorAll('[class*="message-bubble"], [class*="message_bubble"], [class*="message-item"], [class*="message-row"], [class*="msg-item"], .message, .bubble');
                    
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
                            if (
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
                            current = current.parentElement;
                        }
                        
                        // Position-based check: if bubble center is on the right side of the container or aligns right, it's seller
                        if (!isSeller && bestContainer) {
                            const containerRect = bestContainer.getBoundingClientRect();
                            const bubbleRect = b.getBoundingClientRect();
                            if (containerRect.width > 0) {
                                const relativeLeft = (bubbleRect.left - containerRect.left) / containerRect.width;
                                const relativeRight = (containerRect.right - bubbleRect.right) / containerRect.width;
                                if (relativeLeft > 0.4 || relativeRight < 0.1) {
                                    isSeller = true;
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
                        
                        msg_classes = set(re.split(r'[\s\-_]', msg_class.lower()))
                        is_seller = (
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
                                            if relative_left > 0.4 or relative_right < 0.1:
                                                is_seller = True
                            except Exception:
                                pass
                        chat_history.append({
                            "text": msg_text,
                            "isSeller": is_seller
                        })

                if not chat_history:
                    log.info("No message history found, skipping.")
                    continue

                # Force isSeller validation (Bug 6)
                for msg in chat_history:
                    msg_lower = msg["text"].lower()
                    if any(reply.lower() in msg_lower for reply in AUTO_REPLIES.values()):
                        msg["isSeller"] = True
                    if DEFAULT_REPLY.lower()[:30] in msg_lower:
                        msg["isSeller"] = True

                last_msg = chat_history[-1]
                last_msg_text = last_msg["text"]
                last_msg_is_seller = last_msg["isSeller"]
                
                is_assistant_ai = is_assistant_ai_msg(last_msg_text)
                
                # Check if the last message is an image
                is_image = (
                    not last_msg_text.strip() or
                    "[gambar]" in last_msg_text.lower() or
                    "gambar" == last_msg_text.strip().lower() or
                    "[image]" in last_msg_text.lower()
                )

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

                # Bug 1: Double reply prevention
                cache_key = f"{username}:{buyer_message[:50]}"
                if cache_key in replied_cache:
                    log.info("Already replied to '%s' with this message context, skipping.", username)
                    continue

                log.info("Buyer message context: %s", buyer_message[:100])
                if is_assistant_ai:
                    reply_text = "Ada yang bisa dibantu?"
                else:
                    reply_text = await get_ai_reply_ollama(buyer_message)
                    if reply_text == DEFAULT_REPLY:
                        log.warning("👉 UNANSWERED BUYER MESSAGE (Need manual reply): %s", buyer_message)

                # 7. Type and send reply
                log.info("=== REPLY ATTEMPT for user '%s' ===", username)
                log.info("Reply text: %s", reply_text[:80])

                # Find input box — prioritize contenteditable in chat area
                input_box = None
                input_sel_used = "none"

                # Strategy 1: Specific contenteditable selectors in chat panel
                ce_selectors = [
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
