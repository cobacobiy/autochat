"""
Shopee Auto-Reply Bot
Runs as a daemon using Playwright persistent context for session persistence.
"""

import asyncio
import logging
import os
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
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/portal/chat")
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
        # Wait for the chat list to be present
        await page.wait_for_selector("[data-testid='chat-list-item'], .chat-list-item", timeout=10_000)

        # Collect unread threads (those with an unread badge)
        unread_items = await page.query_selector_all(
            "[data-testid='chat-list-item']:has(.unread-badge), "
            ".chat-list-item:has(.unread-count)"
        )

        if not unread_items:
            return 0

        log.info("Found %d unread chat(s)", len(unread_items))

        for item in unread_items:
            try:
                await item.click()
                await page.wait_for_timeout(1500)

                # Read the latest buyer message
                messages = await page.query_selector_all(".message-bubble--buyer, [data-testid='buyer-message']")
                if not messages:
                    continue

                last_msg_el = messages[-1]
                last_msg_text = await last_msg_el.inner_text()
                log.info("Buyer message: %s", last_msg_text[:100])

                reply_text = get_auto_reply(last_msg_text)

                # Type and send reply
                input_box = await page.query_selector(
                    "[data-testid='chat-input'], .chat-input textarea, textarea.chat-input__textarea"
                )
                if not input_box:
                    log.warning("Could not find chat input box — Shopee may have changed its DOM.")
                    continue

                await input_box.click()
                await input_box.fill(reply_text)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(800)

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
    os.makedirs("/data/logs", exist_ok=True)

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
            await asyncio.sleep(300)
            await context.close()
            return

        log.info("Logged in — entering polling loop (every %ds)", POLL_INTERVAL_SECONDS)

        while True:
            try:
                # Reload or navigate to refresh chat list
                await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(2000)

                count = await handle_unread_chats(page)
                if count:
                    log.info("Processed %d chat(s) this cycle", count)
                else:
                    log.debug("No unread chats")

            except Exception as exc:
                log.error("Unexpected error in poll loop: %s", exc, exc_info=True)
                # Brief pause before retrying to avoid hammering on persistent errors
                await asyncio.sleep(15)

            await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_bot())
