import logging
import os

from bot.config import KNOWLEDGE_PATH
from bot.state import bot_state

log = logging.getLogger(__name__)

def parse_knowledge_answers():
    bot_state.knowledge_answers.clear()
    lines = bot_state.knowledge_base.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("T:"):
            question = line[2:].strip().lower()
            j = i + 1
            while j < len(lines):
                next_line = lines[j].strip()
                if next_line.startswith("J:"):
                    answer = next_line[2:].strip()
                    if question and answer:
                        bot_state.knowledge_answers[question] = answer
                    break
                elif next_line.startswith("T:") or next_line.startswith("#"):
                    break
                j += 1
            i = j
        else:
            i += 1

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
