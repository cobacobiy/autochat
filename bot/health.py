import json
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from bot.state import bot_state
from bot.config import AI_PROVIDER, OLLAMA_MODEL, GEMINI_MODEL, ANTHROPIC_MODEL

BOT_START_TIME = time.time()

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        def _get_current_model():
            if AI_PROVIDER == "ollama":
                return OLLAMA_MODEL
            if AI_PROVIDER == "gemini":
                return GEMINI_MODEL
            return ANTHROPIC_MODEL
            
        status = {
            "status": "ok",
            "uptime_seconds": int(time.time() - BOT_START_TIME),
            "ai_provider": AI_PROVIDER,
            "ai_model": _get_current_model(),
            "knowledge_loaded": bool(bot_state.knowledge_base),
            "knowledge_entries": len(bot_state.knowledge_answers),
            "daily_replies": bot_state.daily_reply_counter,
            "daily_skips": bot_state.daily_skip_count,
            "daily_unanswered": bot_state.daily_unanswered_count,
            "daily_ai_replied": bot_state.daily_ai_replied_count,
            "cache_size": len(bot_state.replied_cache),
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(status).encode())
    def log_message(self, format, *args): pass

def start_health_server():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8080), HealthHandler).serve_forever(), daemon=True).start()
