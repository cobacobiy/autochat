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
    Find unread chat threads and reply to each one.
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

        # Wait for the chat list to be present
        await page.wait_for_selector("[data-testid='chat-list-item'], .chat-list-item", timeout=10_000)

        # Collect unread threads (those with an unread badge)
        unread_items = await page.query_selector_all(
            "[data-testid='chat-list-item']:has(.unread-badge), "
            "[data-testid='chat-list-item']:has(.unread-count), "
            "[data-testid='chat-list-item']:has([class*='unread']), "
            ".chat-list-item:has(.unread-badge), "
            ".chat-list-item:has(.unread-count), "
            ".chat-list-item:has([class*='unread'])"
        )

        if not unread_items:
            return 0

        log.info("Found %d unread chat(s)", len(unread_items))

        for item in unread_items:
            try:
                await item.click()
                await page.wait_for_timeout(1500)

                # Identify message bubbles in history
                buyer_selector = (
                    ".message-bubble--buyer, "
                    "[data-testid='buyer-message'], "
                    ".message-bubble.buyer, "
                    "[class*='message-bubble'][class*='buyer'], "
                    "[class*='message-row'][class*='buyer']"
                )
                seller_selector = (
                    ".message-bubble--seller, "
                    "[data-testid='seller-message'], "
                    ".message-bubble.seller, "
                    ".message-bubble.me, "
                    "[class*='message-bubble'][class*='seller'], "
                    "[class*='message-row'][class*='seller'], "
                    "[class*='message-bubble'][class*='me'], "
                    "[class*='message-row'][class*='me']"
                )

                buyer_messages = await page.query_selector_all(buyer_selector)
                seller_messages = await page.query_selector_all(seller_selector)

                if not buyer_messages:
                    log.info("No buyer messages found in history, skipping.")
                    continue

                # Prevent duplicate reply: if seller has already replied after the last buyer message, skip.
                if seller_messages:
                    last_buyer = buyer_messages[-1]
                    last_seller = seller_messages[-1]
                    is_seller_last = await page.evaluate(
                        "(nodes) => (nodes.buyer.compareDocumentPosition(nodes.seller) & 4) > 0",
                        {"buyer": last_buyer, "seller": last_seller}
                    )
                    if is_seller_last:
                        log.info("Seller already replied to the latest message. Skipping.")
                        continue

                # Read the latest buyer message
                last_msg_el = buyer_messages[-1]
                last_msg_text = await last_msg_el.inner_text()
                log.info("Buyer message: %s", last_msg_text[:100])

                reply_text = get_auto_reply(last_msg_text)

                # Type and send reply
                input_box = await page.query_selector(
                    "[data-testid='chat-input'], .chat-input textarea, textarea.chat-input__textarea, textarea"
                )
                if not input_box:
                    log.warning("Could not find chat input box — Shopee may have changed its DOM.")
                    continue

                await input_box.click()
                await input_box.fill(reply_text)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(800)

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

                log.info("Replied: %s", reply_text[:80])
                processed += 1

            except Exception as exc:
                log.error("Error processing chat item: %s", exc)

    except Exception as exc:
        log.error("Error fetching chat list: %s", exc)

    return processed


async def run_bot():
    """Main daemon loop."""
    log.info("Starting Shopee Auto-Reply Bot")
    log.info("Profile directory: %s", PROFILE_DIR)
    log.info("Poll interval: %ds", POLL_INTERVAL_SECONDS)

    os.makedirs(PROFILE_DIR, exist_ok=True)

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
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
            ],
            viewport={"width": 1280, "height": 900},
        )

        page = context.pages[0] if context.pages else await context.new_page()

        log.info("Navigating to Shopee Seller Chat…")
        await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # Check if already logged in
        if "login" in page.url or "auth" in page.url:
            log.warning(
                "Not logged in! Please log in manually via VNC/headful mode, "
                "then restart the bot. Profile will be saved at: %s",
                PROFILE_DIR,
            )
            # Keep browser open so user can log in via VNC
            try:
                await asyncio.wait_for(shutdown_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass
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
