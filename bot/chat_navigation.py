import logging
from bot.state import bot_state
from bot.utils import do_human_delay

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