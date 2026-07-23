import logging
import os

LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/shopee-profile")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/new-webchat/conversations")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))

SHOPEE_USERNAME = os.getenv("SHOPEE_USERNAME", "")
SHOPEE_PASSWORD = os.getenv("SHOPEE_PASSWORD", "")

if SHOPEE_PASSWORD and os.path.exists(".env"):
    logging.warning("⚠️ SHOPEE_PASSWORD is set. Ensure .env file is in .gitignore!")

AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").lower()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
for suffix in ["/api/generate", "/api/chat", "/api"]:
    if OLLAMA_URL.endswith(suffix):
        OLLAMA_URL = OLLAMA_URL[: -len(suffix)]
        break
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

UNANSWERED_PATH = os.getenv("UNANSWERED_PATH", "/app/unanswered_questions.txt")
MAX_DAILY_REPLIES = int(os.getenv("MAX_DAILY_REPLIES", "5000"))
MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", "1000"))

# Constants for magic numbers
FORCE_RELOAD = -1
BROWSER_LIFETIME_SECONDS = int(os.getenv("BROWSER_LIFETIME", "21600"))
CACHE_EXPIRY_SECONDS = 86400
KNOWLEDGE_RELOAD_CYCLES = 120
HEARTBEAT_CYCLES = 60
MAX_CHAT_SCAN_ATTEMPTS = 30
MAX_AI_REPLY_LENGTH = 400
DEFAULT_REPLY = os.getenv("DEFAULT_REPLY", "Ada yang bisa dibantu?")
KNOWLEDGE_PATH = os.getenv("KNOWLEDGE_PATH", "/app/store_knowledge.txt")
AUTO_REPLIES = {
    "harga": "Harga sudah tertera di halaman produk. Silakan cek ya kak 😊",
    "stok": "Stok masih tersedia, silakan langsung order kak!",
    "ongkir": "Ongkir dihitung otomatis oleh Shopee sesuai lokasi kakak.",
    "cod": "Maaf, belum tersedia COD untuk saat ini.",
    "garansi": "Produk bergaransi 30 hari jika ada kerusakan dari pabrik.",
    "pengiriman": "Penjaringan Jakarta Utara",
    "dari mana": "Penjaringan Jakarta Utara",
}

SKIP_MESSAGES = {
    "ok", "oke", "baik", "baik kak", "baik ka", "oke kak", "oke ka",
    "siap", "terima kasih", "makasih", "sami sami", "mks", "thx", "ty",
    "ok kak", "ok ka", "sip", "siap kak", "siap ka", "makasih kak", "makasih ka",
    "nuhun", "suwun", "makasih banyak", "terima kasih banyak",
    "tolong kirim sesuai pesanan", "sesuai pesanan ya", "sesuai pesanan",
    "sama sama", "sama2", "samaa2", "sama-sama", "sama2 kak", "sama2 ka",
    "y", "ya", "ya kak", "ya ka", "iya", "iya kak", "iya ka", "y kak", "y ka"
}

ADMIN_KEYWORDS = {
    "instan", "instant", "gojek", "grab", "sameday", "same day", "gosend"
}

_JS_DIR = os.path.join(os.path.dirname(__file__), "js")

def _load_js(filename: str) -> str:
    path = os.path.join(_JS_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.error("FATAL: JS file not found: %s", path)
        raise SystemExit(f"Required JS file missing: {path}")

IS_SELLER_JS = _load_js("is_seller.js")
GET_CHAT_ITEMS_JS = _load_js("get_chat_items.js")

if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
    logging.warning("AI_PROVIDER is gemini but GEMINI_API_KEY is not set!")
elif AI_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    logging.warning("AI_PROVIDER is claude but ANTHROPIC_API_KEY is not set!")
