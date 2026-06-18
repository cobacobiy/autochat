"""
Shopee Auto-Reply Bot
Runs as a daemon using Playwright persistent context for session persistence.
"""

import asyncio
import logging
import os
import signal
import sys

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

AUTO_REPLIES = {
    "harga": "Harga sudah tertera di halaman produk. Silakan cek ya kak 😊",
    "stok": "Stok masih tersedia, silakan langsung order kak!",
    "ongkir": "Ongkir dihitung otomatis oleh Shopee sesuai lokasi kakak.",
    "cod": "Maaf, belum tersedia COD untuk saat ini.",
    "garansi": "Produk bergaransi 30 hari jika ada kerusakan dari pabrik.",
}
DEFAULT_REPLY = "Halo kak! Terima kasih sudah menghubungi kami. Tim kami akan segera membalas 😊"


# ── Bot logic ──────────────────────────────────────────────────────────────────
def get_auto_reply(message: str) -> str:
    """Match message keywords to canned replies."""
    msg_lower = message.lower()
    for keyword, reply in AUTO_REPLIES.items():
        if keyword in msg_lower:
            return reply
    return DEFAULT_REPLY


async def handle_unread_chats(page) -> int:
    """
    Find unread and Assistant AI chats, and reply to each one.
    Returns the number of chats processed.
    """
    processed = 0
    try:
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
                await page.wait_for_timeout(1000)
        except Exception as e:
            log.warning("Clicking 'Semua Chat' tab failed: %s", e)

        # Debug DOM and Frames
        try:
            log.info("Page frames count: %d", len(page.frames))
            for i, f in enumerate(page.frames):
                log.info("Frame #%d URL: %s, Name: %s", i, f.url, f.name)
                try:
                    debug_info = await f.evaluate("""() => {
                        const results = [];
                        const elements = [...document.querySelectorAll('*')].filter(e => e.textContent.includes('leonman18'));
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
                    }""")
                    if debug_info:
                        log.info("Frame #%d DOM DEBUG: %s", i, debug_info)
                except Exception as fe:
                    log.warning("Frame #%d eval failed: %s", i, fe)
        except Exception as e:
            log.warning("DOM Debug failed: %s", e)

        # Wait for the chat list to be present (short timeout for debug)
        try:
            await page.wait_for_selector("[data-testid='chat-list-item'], .chat-list-item, [class*='chat-list-item']", timeout=3000)
        except Exception as wait_err:
            log.warning("Wait for selector timed out, proceeding anyway: %s", wait_err)

        # Collect all chat items in the sidebar
        chat_items = await page.query_selector_all(
            "[data-testid='chat-list-item'], "
            ".chat-list-item, "
            "[class*='chat-list-item'], "
            "[class*='conversation-item']"
        )

        if not chat_items:
            return 0

        log.info("Found %d chat item(s) in sidebar list", len(chat_items))

        for index, item in enumerate(chat_items):
            try:
                # 3. Read sidebar item properties to determine if we should skip
                item_text = await item.inner_text()
                
                # Check for unread badge or indicator
                has_unread = await item.query_selector(
                    ".unread-badge, .unread-count, [class*='unread'], [class*='badge']"
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
                        # Seller appears to have replied, skip clicking this chat
                        continue

                log.info("Processing chat #%d: %s", index + 1, item_text.replace('\n', ' | ')[:80])
                await item.click()
                await page.wait_for_timeout(2000)

                # 4. Identify message bubbles in history
                message_selector = (
                    ".message-bubble, "
                    "[class*='message-bubble'], "
                    "[class*='message-row'], "
                    "[class*='message-item']"
                )
                messages = await page.query_selector_all(message_selector)

                if not messages:
                    log.info("No message bubbles found in history, skipping.")
                    continue

                # 5. Check if the last message in history is from the seller
                last_msg = messages[-1]
                last_text = await last_msg.inner_text()
                
                is_last_seller = False
                last_class = await last_msg.get_attribute("class") or ""
                last_style = await last_msg.get_attribute("style") or ""
                if (
                    "seller" in last_class.lower() or 
                    "me" in last_class.lower() or 
                    "right" in last_class.lower() or 
                    "right" in last_style.lower()
                ):
                    is_last_seller = True
                else:
                    # Check parent classes
                    parent = await last_msg.query_selector("xpath=..")
                    if parent:
                        parent_class = await parent.get_attribute("class") or ""
                        if "seller" in parent_class.lower() or "me" in parent_class.lower() or "right" in parent_class.lower():
                            is_last_seller = True

                is_assistant_ai = "[asisten ai" in last_text.lower()
                
                # If the last message is from the seller AND it's not Assistant AI, skip
                if is_last_seller and not is_assistant_ai:
                    log.info("Seller already replied to the latest message. Skipping.")
                    continue

                # 6. Extract all buyer messages to generate the reply from
                buyer_messages = []
                for msg in messages:
                    msg_class = await msg.get_attribute("class") or ""
                    msg_style = await msg.get_attribute("style") or ""
                    is_msg_seller = (
                        "seller" in msg_class.lower() or 
                        "me" in msg_class.lower() or 
                        "right" in msg_class.lower() or 
                        "right" in msg_style.lower()
                    )
                    if not is_msg_seller:
                        parent = await msg.query_selector("xpath=..")
                        if parent:
                            parent_class = await parent.get_attribute("class") or ""
                            if "seller" in parent_class.lower() or "me" in parent_class.lower() or "right" in parent_class.lower():
                                is_msg_seller = True
                    
                    if not is_msg_seller:
                        buyer_messages.append(msg)

                # Get latest buyer message text
                if buyer_messages:
                    last_buyer_msg = buyer_messages[-1]
                    last_msg_text = await last_buyer_msg.inner_text()
                else:
                    last_msg_text = last_text

                log.info("Buyer message context: %s", last_msg_text[:100])
                reply_text = get_auto_reply(last_msg_text)

                # 7. Type and send reply
                input_box = await page.query_selector(
                    "[data-testid='chat-input'], "
                    ".chat-input textarea, "
                    "textarea.chat-input__textarea, "
                    "textarea, "
                    "[contenteditable='true']"
                )
                if not input_box:
                    log.warning("Could not find chat input box — Shopee may have changed its DOM.")
                    continue

                await input_box.click()
                await input_box.fill(reply_text)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(1000)

                # Click send button if still visible and exists
                try:
                    send_button = page.locator(
                        "button:has-text('Kirim'), "
                        "button:has-text('Send'), "
                        "[data-testid='send-button'], "
                        "button.send-btn, "
                        "button[class*='send']"
                    ).first
                    if await send_button.is_visible():
                        log.info("Clicking the Send/Kirim button...")
                        await send_button.click()
                        await page.wait_for_timeout(800)
                except Exception as send_err:
                    log.debug("Send button click failed or not found (sent by Enter): %s", send_err)

                log.info("Replied successfully: %s", reply_text[:80])
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
                lambda: ask_exit(signame)
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
                count = await handle_unread_chats(page)
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
