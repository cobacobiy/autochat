import random
import os
import time
import logging

log = logging.getLogger(__name__)

def cleanup_old_screenshots(log_dir, hours=24):
    try:
        now = time.time()
        for f in os.listdir(log_dir):
            if f.endswith('.png'):
                filepath = os.path.join(log_dir, f)
                if os.stat(filepath).st_mtime < now - hours * 3600:
                    os.remove(filepath)
    except Exception as e:
        log.warning("Failed to clean up screenshots: %s", e)

def is_assistant_ai_msg(text: str) -> bool:
    """Check if the text indicates it's from Assistant AI or an Auto-Reply."""
    t = text.lower()
    return (
        "[asisten ai" in t or 
        "asisten ai toko" in t or 
        "ai asistent toko" in t or 
        "asistent ai" in t or
        "dikirim oleh asisten ai" in t or
        "dikirim oleh asisten" in t or
        "auto-reply" in t or
        "auto reply" in t or
        "kami akan segera membalas" in t or
        "variant yg bisa di klik" in t
    )

async def do_human_delay(page, min_ms=2000, max_ms=4500):
    delay = random.randint(min_ms, max_ms)
    await page.wait_for_timeout(delay)

