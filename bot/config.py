import os

LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/shopee-profile")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/new-webchat/conversations")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))

SHOPEE_USERNAME = os.getenv("SHOPEE_USERNAME", "")
SHOPEE_PASSWORD = os.getenv("SHOPEE_PASSWORD", "")

AI_PROVIDER = os.getenv("AI_PROVIDER", "ollama").lower()

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
if OLLAMA_URL.endswith("/api/generate") or OLLAMA_URL.endswith("/api/chat"):
    OLLAMA_URL = OLLAMA_URL.rsplit("/api/", 1)[0]
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")

UNANSWERED_PATH = os.getenv("UNANSWERED_PATH", "/app/unanswered_questions.txt")
MAX_DAILY_REPLIES = int(os.getenv("MAX_DAILY_REPLIES", "5000"))
MAX_CACHE_SIZE = int(os.getenv("MAX_CACHE_SIZE", "1000"))
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
    "tolong kirim sesuai pesanan", "sesuai pesanan ya", "sesuai pesanan"
}

_JS_DIR = os.path.join(os.path.dirname(__file__), "js")

with open(os.path.join(_JS_DIR, "is_seller.js"), encoding="utf-8") as _f:
    IS_SELLER_JS = _f.read()

with open(os.path.join(_JS_DIR, "get_chat_items.js"), encoding="utf-8") as _f:
    GET_CHAT_ITEMS_JS = _f.read()

if AI_PROVIDER == "gemini" and not GEMINI_API_KEY:
    import logging
    logging.warning("AI_PROVIDER is gemini but GEMINI_API_KEY is not set!")
elif AI_PROVIDER == "claude" and not ANTHROPIC_API_KEY:
    import logging
    logging.warning("AI_PROVIDER is claude but ANTHROPIC_API_KEY is not set!")
