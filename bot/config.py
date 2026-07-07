import os

LOG_DIR = os.getenv("LOG_DIR", "/data/logs")
LOG_FORMAT = os.getenv("LOG_FORMAT", "text").lower()

PROFILE_DIR = os.getenv("PROFILE_DIR", "/data/shopee-profile")
SHOPEE_CHAT_URL = os.getenv("SHOPEE_CHAT_URL", "https://seller.shopee.co.id/new-webchat/conversations")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "5"))

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
    "nuhun", "suwun"
}

IS_SELLER_JS = r"""
function isSeller(el, container) {
    let current = el;
    for (let depth = 0; depth < 15; depth++) {
        if (!current) break;
        
        // 1. Periksa data-cy
        const dataCy = current.getAttribute('data-cy') || '';
        if (dataCy.includes('send') || dataCy.includes('seller') || dataCy.includes('to-user')) return true;
        if (dataCy.includes('receive') || dataCy.includes('buyer') || dataCy === 'webchat-message-receive') return false;
        
        // 2. Periksa nama class
        const className = (current.className || '').toString().toLowerCase();
        if (className.includes('send') || className.includes('seller') || 
            className.includes('self') || className.includes('right')) return true;
        if (className.includes('receive') || className.includes('buyer') || className.includes('left')) return false;

        // 3. Periksa CSS alignment (align-self, justify-content, dsb.)
        const style = window.getComputedStyle(current);
        if (style.justifyContent === 'flex-end' || style.textAlign === 'right' || 
            style.alignItems === 'flex-end' || style.flexDirection === 'row-reverse' ||
            style.alignSelf === 'flex-end' || style.justifySelf === 'end') return true;
        
        current = current.parentElement;
    }
    
    // Fallback berdasarkan posisi relatif terhadap kontainer
    if (container && container !== document.body) {
        const cRect = container.getBoundingClientRect();
        const bRect = el.getBoundingClientRect();
        if (cRect.width > 0) {
            const relLeft = (bRect.left - cRect.left) / cRect.width;
            const bubbleCenter = bRect.left + (bRect.width / 2);
            const containerCenter = cRect.left + (cRect.width / 2);
            if (relLeft > 0.4 || bubbleCenter > containerCenter + 10) return true;
            if (bubbleCenter < containerCenter - 10) return false;
        }
    }
    
    // Fallback berdasarkan warna background
    const bubbleStyle = window.getComputedStyle(el);
    const bgColor = bubbleStyle.backgroundColor || '';
    if (bgColor && (
        bgColor.includes('238') ||
        bgColor.includes('255, 87') ||
        bgColor.includes('ee4d2d') ||
        bgColor.includes('232, 245') ||
        bgColor.includes('234, 245') ||
        bgColor.includes('214, 255') ||
        bgColor.includes('204, 255') ||
        el.closest('[class*="seller"]') ||
        el.closest('[class*="right"]') ||
        el.closest('[class*="send"]')
    )) {
        return true;
    }
    
    return false;
}
"""



GET_CHAT_ITEMS_JS = r"""() => {
    // Ambil elemen chat dari daftar sidebar kiri (maksimal 5 teratas)
    const cells = document.querySelectorAll('[data-cy^="webchat-conversation-cell-root"], li');
    if (cells.length > 0) {
        return Array.from(cells).slice(0, 5); 
    }
    
    // Fallback
    const allDivs = [...document.querySelectorAll('div')];
    const fallbackCells = [];
    for (const div of allDivs) {
        const text = div.textContent || '';
        const hasTimestamp = /\b\d{2}:\d{2}\b/.test(text) || text.includes('Yesterday') || text.includes('Kemarin');
        if (hasTimestamp && text.length > 5 && text.length < 300) {
            const rect = div.getBoundingClientRect();
            if (rect.left > 0 && rect.left < window.innerWidth * 0.4 && rect.height > 20 && rect.height < 150) {
                fallbackCells.push(div);
                if (fallbackCells.length >= 5) break;
            }
        }
    }
    return fallbackCells;
}"""
