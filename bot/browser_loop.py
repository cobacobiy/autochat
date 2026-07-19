import asyncio
import logging
import os
import signal
import subprocess
import time
import random
from datetime import datetime

from playwright.async_api import async_playwright

from bot.config import (
    LOG_DIR, PROFILE_DIR, SHOPEE_CHAT_URL, POLL_INTERVAL_SECONDS, MAX_CACHE_SIZE, 
    SHOPEE_USERNAME, SHOPEE_PASSWORD, BROWSER_LIFETIME_SECONDS, KNOWLEDGE_RELOAD_CYCLES,
    CACHE_EXPIRY_SECONDS, HEARTBEAT_CYCLES
)
from bot.state import bot_state
from bot.knowledge import reload_knowledge
from bot.utils import do_human_delay, cleanup_old_screenshots
from bot.shopee_browser import handle_unread_chats

log = logging.getLogger(__name__)

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
                    if os.name == 'nt':
                        subprocess.run(["taskkill", "/F", "/IM", "chrome.exe", "/T"], capture_output=True)
                    else:
                        subprocess.run(["pkill", "-f", "chrome"], capture_output=True)
                        subprocess.run(["pkill", "-f", "chromium"], capture_output=True)
                    
                    await asyncio.wait_for(shutdown_event.wait(), timeout=5)
                    log.error("Restarting script to ensure clean Playwright state...")
                    break
                # Anti-bot stealth: override navigator properties
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en-US', 'en'] });
                    window.chrome = { runtime: {} };
                    if (navigator.__proto__.webdriver !== undefined) delete navigator.__proto__.webdriver;
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

                # Check if already logged in (ignore is_from_login query param)
                is_logged_out = ("/login" in page.url.lower() or "/auth" in page.url.lower() or "/verify" in page.url.lower() or "captcha" in page.url.lower() or "challenge" in page.url.lower())
                
                if is_logged_out:
                    log.warning(
                        "Not logged in! Please log in manually via VNC/headful mode. "
                        "The bot will automatically resume once login is detected. "
                        "Profile will be saved at: %s",
                        PROFILE_DIR,
                    )
                    
                    if SHOPEE_USERNAME and SHOPEE_PASSWORD:
                        delay_seconds = random.randint(300, 420)
                        log.info("SHOPEE_USERNAME and SHOPEE_PASSWORD found! Menunggu jeda %d detik (5-7 menit) sebelum auto-login...", delay_seconds)
                        
                        try:
                            await asyncio.wait_for(shutdown_event.wait(), timeout=delay_seconds)
                        except asyncio.TimeoutError:
                            pass
                            
                        if not shutdown_event.is_set() and ("/login" in page.url.lower() or "/auth" in page.url.lower()) and "is_from_login=true" not in page.url.lower():
                            log.info("Memulai proses auto-login setelah jeda...")
                            try:
                                # Wait for login fields or language popup to be visible
                                try:
                                    lang_btn = page.locator('button:has-text("Bahasa Indonesia")').first
                                    if await lang_btn.is_visible():
                                        log.info("Pop-up pilihan bahasa terdeteksi. Memilih 'Bahasa Indonesia'...")
                                        await lang_btn.click()
                                        await page.wait_for_timeout(1500)
                                except Exception:
                                    pass

                                await page.wait_for_selector('input[type="text"], input[name="loginKey"]', timeout=10000)
                                user_input = page.locator('input[type="text"], input[name="loginKey"]').first
                                pass_input = page.locator('input[type="password"], input[name="password"]').first
                                login_btn = page.locator('button:has-text("Log In"), button:has-text("Log in"), button:has-text("Login")').first
                                
                                if await user_input.is_visible() and await pass_input.is_visible():
                                    await user_input.fill(SHOPEE_USERNAME)
                                    await page.wait_for_timeout(1000)
                                    await pass_input.fill(SHOPEE_PASSWORD)
                                    await page.wait_for_timeout(1000)
                                    await login_btn.click()
                                    log.info("Auto-login submitted! Waiting to see if OTP/Captcha is required...")
                                    await page.wait_for_timeout(5000)
                            except Exception as e:
                                log.error("Auto-login attempt failed (maybe UI changed or already logged in): %s", e)

                    # Poll page URL to detect when user logs in (or solves Captcha/OTP if auto-login was partially successful)
                    login_detected = False
                    for _ in range(120): # 120 * 5s = 600s = 10 minutes
                        if shutdown_event.is_set():
                            break
                        try:
                            await page.wait_for_timeout(5000)
                            # Check if we are logged in now
                            current_is_logged_out = ("/login" in page.url.lower() or "/auth" in page.url.lower() or "/verify" in page.url.lower() or "captcha" in page.url.lower() or "challenge" in page.url.lower())
                            if not current_is_logged_out:
                                log.info("Login detected! Starting polling loop...")
                                login_detected = True
                                await page.wait_for_timeout(3000)
                                break
                        except Exception as e:
                            log.warning("Connection lost or browser closed during login check: %s", e)
                            break
                    
                    if not login_detected:
                        log.info("Closing persistent Chromium context...")
                        try:
                            await context.close()
                        except Exception:
                            pass
                        break

                log.info("Logged in — entering polling loop (every %ds)", POLL_INTERVAL_SECONDS)

                # Define browser lifetime
                browser_lifetime_limit = BROWSER_LIFETIME_SECONDS
                last_page_reload_time = time.time()

                while not shutdown_event.is_set():
                    try:
                        # Check if we need to restart the entire browser (e.g., reached lifetime limit)
                        if time.time() - browser_start_time > browser_lifetime_limit:
                            log.info("Browser reached lifetime limit (%d seconds). Scheduling restart...", browser_lifetime_limit)
                            break
                        current_date = datetime.now().strftime("%Y-%m-%d")
                        if current_date != bot_state.daily_reply_date:
                            bot_state.daily_reply_date = current_date
                            bot_state.sent_messages.clear()
                            log.info("Daily reset: Cleared bot_state.sent_messages cache.")

                        # Enforce MAX_CACHE_SIZE limit
                        if len(bot_state.replied_cache) > MAX_CACHE_SIZE:
                            sorted_items = sorted(bot_state.replied_cache.items(), key=lambda x: x[1])
                            for k, _ in sorted_items[:len(sorted_items) // 5]:
                                del bot_state.replied_cache[k]
                            log.info("Cache trimmed from >%d to %d entries", MAX_CACHE_SIZE, len(bot_state.replied_cache))
    
                        cycle_count += 1
                        
                        # Heartbeat logging
                        if cycle_count % HEARTBEAT_CYCLES == 0:
                            log.info("💓 Bot heartbeat: %d cycles completed, bot_state.replied_cache size: %d", 
                                     cycle_count, len(bot_state.replied_cache))
    
                        # Hot reload store_knowledge.txt
                        if cycle_count % KNOWLEDGE_RELOAD_CYCLES == 0:
                            reload_knowledge()
                            cleanup_old_screenshots(LOG_DIR, 24)
    
                        # Clean up expired bot_state.replied_cache items
                        now = time.time()
                        expired = [k for k, v in bot_state.replied_cache.items() if now - v > CACHE_EXPIRY_SECONDS]
                        for k in expired:
                            del bot_state.replied_cache[k]
    
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
                            
                            # Cek popup error UI Shopee ("Terjadi Kesalahan" / "An Error Occurred")
                            try:
                                coba_lagi_btn = page.locator("text=/Coba Lagi|Try Again/i >> visible=true").first
                                if await coba_lagi_btn.is_visible(timeout=1000):
                                    log.warning("🚨 Muncul popup error dari Shopee. Menandai untuk reload...")
                                    has_crash_text = True
                                else:
                                    html_content = (await page.content()).lower()
                                    has_error = "terjadi kesalahan" in html_content or "an error occurred" in html_content or "something went wrong" in html_content
                                    has_retry = "coba lagi" in html_content or "try again" in html_content or "memuat halaman" in html_content
                                    if has_error and has_retry:
                                        log.warning("🚨 Muncul popup error dari Shopee (HTML). Menandai untuk reload...")
                                        has_crash_text = True
                            except Exception:
                                pass
                            
                            is_logged_out_check = ("/login" in page.url.lower() or "/auth" in page.url.lower()) and "is_from_login=true" not in page.url.lower()
                            if (is_blank and not is_logged_out_check) or has_crash_text:
                                log.warning("🚨 TERDETEKSI HALAMAN BLANK PUTIH ATAU CRASH! Menunggu jeda manusiawi sebelum navigasi ulang...")
                                await do_human_delay(page, 3000, 7000)
                                try:
                                    # Navigasi ke beranda seller centre dulu untuk mereset state Shopee Webchat
                                    await page.goto("https://seller.shopee.co.id/", wait_until="domcontentloaded", timeout=30000)
                                    await page.wait_for_timeout(3000)
                                    # Kembali ke halaman chat
                                    await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                                    await page.wait_for_timeout(5000)
                                    bot_state.has_setup_tabs = False
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
                        is_logged_out_inner = ("/login" in page.url.lower() or "/auth" in page.url.lower() or "/verify" in page.url.lower() or "captcha" in page.url.lower() or "challenge" in page.url.lower())
                        if is_logged_out_inner:
                            log.warning("Detected logout/redirect to login page. Retrying navigation...")
                            await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                            if ("/login" in page.url.lower() or "/auth" in page.url.lower() or "/verify" in page.url.lower() or "captcha" in page.url.lower() or "challenge" in page.url.lower()):
                                log.error("Still not logged in. Breaking out to main loop for auto-login sequence...")
                                break
    
                        # Scheduled page reload every 2 hours (7200 seconds)
                        if time.time() - last_page_reload_time > 7200:
                            log.info("🕒 Scheduled periodic page reload (every 2 hours)...")
                            try:
                                await page.goto("https://seller.shopee.co.id/", wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(3000)
                                await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(5000)
                                bot_state.has_setup_tabs = False
                                last_page_reload_time = time.time()
                                continue
                            except Exception as e:
                                log.error("Failed to perform scheduled page reload: %s", e)
    
                        # Scan and reply to unread chats directly on the live page
                        count = await handle_unread_chats(page)
                        if count == -1:
                            log.warning("🔄 Force reload dipicu oleh popup error di tengah pembacaan chat! Menunggu jeda manusiawi...")
                            await do_human_delay(page, 3000, 7000)
                            try:
                                await page.goto("https://seller.shopee.co.id/", wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(3000)
                                await page.goto(SHOPEE_CHAT_URL, wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(5000)
                                bot_state.has_setup_tabs = False
                            except Exception as e:
                                log.error("Gagal navigasi ulang (Force Reload): %s", e)
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