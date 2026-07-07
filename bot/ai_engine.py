import re
import httpx
import logging
import asyncio
from bot.state import bot_state
from bot.config import (
    AUTO_REPLIES, AI_PROVIDER, OLLAMA_URL, OLLAMA_MODEL, 
    GEMINI_API_KEY, GEMINI_MODEL, ANTHROPIC_API_KEY, ANTHROPIC_MODEL
)

log = logging.getLogger(__name__)

# ── Bot logic ────────────────────────────────────────
def get_auto_reply(message: str) -> str:
    """Fallback when AI fails or times out."""
    msg = message.lower()
    for keyword, reply in AUTO_REPLIES.items():
        if re.search(rf'\b{re.escape(keyword)}\b', msg):
            return reply
    return "TIDAK TAHU"

def build_system_prompt() -> str:
    return (
        "Anda adalah Asisten Customer Service toko online yang ramah, sopan, dan luwes.\n\n"
        f"=== KNOWLEDGE BASE ===\n{bot_state.knowledge_base}\n====================\n\n"
        "Aturan Menjawab:\n"
        "1. Jawab pertanyaan spesifik mengenai produk berdasarkan [KNOWLEDGE BASE].\n"
        "2. Jika pembeli meminta pilih motif/warna, jawab: \"Halo kak! Untuk pilihan motif atau warna, silakan tuliskan di Catatan Pembeli saat checkout ya kak 😊\"\n"
        "3. Jika pembeli meminta dikirim cepat (buru-buru/kapan dikirim), jawab: \"Pesanan kakak akan segera kami proses dan kirimkan sesuai antrean ya kak, mohon ditunggu 😊\"\n"
        "4. Gunakan akal sehat ala CS manusia. Jika ada sapaan atau obrolan santai, balaslah dengan ramah.\n"
        "5. Jika pembeli menanyakan detail spesifik produk, mengajukan komplain pesanan kurang/salah kirim, meminta pengembalian dana (refund), atau hal lain yang benar-benar TIDAK ADA solusinya di [KNOWLEDGE BASE], Anda WAJIB menjawab HANYA dengan kata: TIDAK TAHU (tanpa ada teks atau kalimat tambahan apa pun).\n"
        "6. Jawab sesingkat dan se-natural mungkin, tidak perlu kaku.\n\n"
        "=== CONTOH CARA MENJAWAB ===\n"
        "Contoh 1 (Komplain barang kurang / salah kirim):\n"
        "Pembeli: \"Pesen isi 50pcs kok dikirim 10pcs\"\n"
        "Jawaban: TIDAK TAHU\n\n"
        "Contoh 2 (Meminta refund / retur):\n"
        "Pembeli: \"Kirim kurangnya atau pengembalian dana aja\"\n"
        "Jawaban: TIDAK TAHU\n\n"
        "Contoh 3 (Tanya spesifikasi tidak ada di Knowledge Base):\n"
        "Pembeli: \"Apakah sampul mika ini anti air dan tebal?\"\n"
        "Jawaban: TIDAK TAHU\n\n"
        "Contoh 4 (Tanya hal yang ada di Knowledge Base):\n"
        "Pembeli: \"Bisa COD ga kak?\"\n"
        "Jawaban: \"Bisa kak, kita suda aktifkan semua COD, jika lom bisa coba pakai akun lain ya kak\"\n"
        "============================="
    )

async def get_ai_reply(buyer_message: str) -> str:
    system_prompt = build_system_prompt()
    
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                if AI_PROVIDER == "gemini":
                    if not GEMINI_API_KEY:
                        log.error("GEMINI_API_KEY is not set!")
                        return "TIDAK TAHU"
                    # Default to gemini-flash-latest as 1.5 is deprecated
                    deprecated_gemini_models = ["gemini-1.5-flash", "gemini-1.5-pro"]
                    model_name = GEMINI_MODEL if GEMINI_MODEL and GEMINI_MODEL not in deprecated_gemini_models else "gemini-flash-latest"
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
                    payload = {
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"parts": [{"text": buyer_message}]}],
                        "generationConfig": {"temperature": 0.0, "topP": 0.1}
                    }
                    resp = await client.post(url, json=payload)
                    if resp.status_code == 200:
                        try:
                            reply = resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
                            return _clean_ai_reply(reply)
                        except (KeyError, IndexError) as e:
                            log.warning("Unexpected Gemini response format: %s", e)
                    else:
                        log.warning("Gemini attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        
                elif AI_PROVIDER == "claude":
                    if not ANTHROPIC_API_KEY:
                        log.error("ANTHROPIC_API_KEY is not set!")
                        return "TIDAK TAHU"
                    url = "https://api.anthropic.com/v1/messages"
                    headers = {
                        "x-api-key": ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json"
                    }
                    payload = {
                        "model": ANTHROPIC_MODEL,
                        "system": system_prompt,
                        "messages": [{"role": "user", "content": buyer_message}],
                        "max_tokens": 512,
                        "temperature": 0.0
                    }
                    resp = await client.post(url, headers=headers, json=payload)
                    if resp.status_code == 200:
                        try:
                            reply = resp.json()["content"][0]["text"].strip()
                            return _clean_ai_reply(reply)
                        except (KeyError, IndexError) as e:
                            log.warning("Unexpected Claude response format: %s", e)
                    else:
                        log.warning("Claude attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        
                else:
                    # Default fallback to Ollama
                    resp = await client.post(f"{OLLAMA_URL}/api/chat", json={
                        "model": OLLAMA_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": buyer_message}
                        ],
                        "stream": False,
                        "options": {"temperature": 0.0, "top_p": 0.1, "num_predict": 200}
                    })
                    if resp.status_code == 200:
                        reply = resp.json().get("message", {}).get("content", "").strip()
                        if reply:
                            return _clean_ai_reply(reply)
                    else:
                        log.warning("Ollama attempt %d returned status %s: %s", attempt + 1, resp.status_code, resp.text)
                        if resp.status_code == 503:
                            log.info("Ollama is loading model, waiting longer...")
                            await asyncio.sleep(10)
                        
        except Exception as e:
            log.warning("%s attempt %d error: %s", AI_PROVIDER.capitalize(), attempt + 1, repr(e))
        
        if attempt < 2:
            await asyncio.sleep(2 ** attempt)
            
    return "TIDAK TAHU"

def _clean_ai_reply(reply: str) -> str:
    reply = reply.strip()
    reply_lower = reply.lower()
    if reply_lower.startswith("j:"):
        reply = reply[2:].strip()
    elif reply_lower.startswith("j :"):
        reply = reply[3:].strip()
    elif reply_lower.startswith("anda:"):
        reply = reply[5:].strip()
    elif reply_lower.startswith("anda :"):
        reply = reply[6:].strip()
    elif reply_lower.startswith("jawaban:"):
        reply = reply[8:].strip()
        
    if "t:" in reply.lower() and "\nj:" in reply.lower():
        log.warning("AI hallucinated Q&A format. Forcing TIDAK TAHU.")
        return "TIDAK TAHU"
        
    if len(reply) > 400:
        log.warning("AI reply is suspiciously long (%d chars), likely a hallucination loop. Forcing TIDAK TAHU.", len(reply))
        return "TIDAK TAHU"
        
    return reply

