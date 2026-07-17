import logging
from bot.config import FORCE_RELOAD
from bot.utils import do_human_delay
from typing import Union
from playwright.async_api import Page

log = logging.getLogger(__name__)

async def send_reply(page: Page, reply_text: str, username: str) -> Union[bool, int]:
    """Kirim balasan chat ke pembeli.
    
    Args:
        page: Playwright Page instance.
        reply_text: Teks balasan.
        username: Username pembeli.
        
    Returns:
        True jika sukses, False jika gagal (skip), atau FORCE_RELOAD (-1) jika terhalang captcha.
    """
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
        return FORCE_RELOAD

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