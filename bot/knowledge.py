import logging
import os

from bot.config import KNOWLEDGE_PATH
from bot.state import bot_state

log = logging.getLogger(__name__)

def parse_knowledge_answers():
    bot_state.knowledge_answers.clear()
    for line in bot_state.knowledge_base.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            parts = line.split("|", 1)
            bot_state.knowledge_answers[parts[0].strip().lower()] = parts[1].strip()

def reload_knowledge():
    """Load FAQ from TXT file based on KNOWLEDGE_PATH."""
    try:
        paths_to_try = [
            KNOWLEDGE_PATH,
            "store_knowledge.txt",
            "../store_knowledge.txt",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "store_knowledge.txt")
        ]
        
        for p_path in paths_to_try:
            if os.path.exists(p_path):
                with open(p_path, "r", encoding="utf-8") as f:
                    new_content = f.read().strip()
                if new_content and new_content != bot_state.knowledge_base:
                    bot_state.knowledge_base = new_content
                    parse_knowledge_answers()
                    log.info("🔄 Knowledge base reloaded dari: %s", p_path)
                return True
        log.warning("File store_knowledge.txt tidak ditemukan di jalur manapun!")
    except Exception as e:
        log.error("Gagal membaca knowledge base: %s", e)
    return False
