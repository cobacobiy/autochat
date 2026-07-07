from bot.config import (
    LOG_DIR, LOG_FORMAT, PROFILE_DIR, SHOPEE_CHAT_URL, POLL_INTERVAL_SECONDS,
    AI_PROVIDER, OLLAMA_MODEL, GEMINI_MODEL, ANTHROPIC_MODEL,
    MAX_DAILY_REPLIES, MAX_CACHE_SIZE, DEFAULT_REPLY
)

def test_config_defaults():
    assert isinstance(LOG_DIR, str)
    assert LOG_FORMAT in ("text", "json")
    assert isinstance(PROFILE_DIR, str)
    assert SHOPEE_CHAT_URL.startswith("http")
    assert isinstance(POLL_INTERVAL_SECONDS, int)
    
    assert AI_PROVIDER in ("ollama", "gemini", "claude")
    assert isinstance(OLLAMA_MODEL, str)
    assert isinstance(GEMINI_MODEL, str)
    assert isinstance(ANTHROPIC_MODEL, str)
    
    assert isinstance(MAX_DAILY_REPLIES, int)
    assert MAX_DAILY_REPLIES > 0
    assert isinstance(MAX_CACHE_SIZE, int)
    assert MAX_CACHE_SIZE > 0
    assert isinstance(DEFAULT_REPLY, str)
