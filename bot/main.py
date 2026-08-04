"""
Shopee Auto-Reply Bot
Runs as a daemon using Playwright persistent context for session persistence.
"""

import asyncio
import logging
import os
import shutil
import sys

from bot.browser_loop import run_bot
from bot.config import KNOWLEDGE_PATH, LOG_DIR, LOG_FORMAT, UNANSWERED_PATH
from bot.health import start_health_server

# ── Logging & Directory setup ──────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

handlers = [logging.StreamHandler(sys.stdout)]
if "SUPERVISOR_PROCESS_NAME" not in os.environ:
    handlers.append(logging.FileHandler(LOG_FILE))

if LOG_FORMAT == "json":
    import json as json_lib
    class JsonFormatter(logging.Formatter):
        def format(self, record):
            return json_lib.dumps({
                "ts": self.formatTime(record),
                "level": record.levelname,
                "msg": record.getMessage()
            })
    formatter = JsonFormatter()
    for h in handlers:
        h.setFormatter(formatter)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
else:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )

log = logging.getLogger(__name__)

# Ensure required files exist
for fpath in [UNANSWERED_PATH, KNOWLEDGE_PATH]:
    if os.path.isdir(fpath):
        log.warning("'%s' is a directory! Attempting to remove...", fpath)
        try:
            shutil.rmtree(fpath)
            with open(fpath, "w") as f:
                f.write("")
        except Exception as err:
            log.warning("Cannot remove directory mount '%s' (%s). Continuing without crash.", fpath, err)
    elif not os.path.exists(fpath):
        try:
            with open(fpath, "w") as f:
                f.write("")
        except Exception as err:
            log.warning("Failed to create file '%s': %s", fpath, err)

# Start HTTP Health Server
start_health_server()

if __name__ == "__main__":
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        log.info("Program dihentikan oleh user.")
    except Exception as e:
        log.fatal("Program crash: %s", e)
