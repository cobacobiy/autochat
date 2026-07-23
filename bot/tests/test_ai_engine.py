from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.ai_engine import get_ai_reply


@pytest.mark.asyncio
async def test_get_ai_reply_ollama():
    with patch("bot.ai_engine.AI_PROVIDER", "ollama"), \
         patch("bot.ai_engine.OLLAMA_URL", "http://test:11434"), \
         patch("bot.ai_engine.httpx.AsyncClient") as mock_client:
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"message": {"content": "Halo kak!"}}
        
        mock_context = AsyncMock()
        mock_context.post.return_value = mock_resp
        mock_client.return_value.__aenter__.return_value = mock_context
        
        reply = await get_ai_reply("Ready kak?")
        assert reply == "Halo kak!"

@pytest.mark.asyncio
async def test_get_ai_reply_gemini():
    with patch("bot.ai_engine.AI_PROVIDER", "gemini"), \
         patch("bot.ai_engine.GEMINI_API_KEY", "test_key"), \
         patch("bot.ai_engine.httpx.AsyncClient") as mock_client:
        
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"candidates": [{"content": {"parts": [{"text": "Hai dari Gemini!"}]}}]}
        
        mock_context = AsyncMock()
        mock_context.post.return_value = mock_resp
        mock_client.return_value.__aenter__.return_value = mock_context
        
        reply = await get_ai_reply("Test")
        assert reply == "Hai dari Gemini!"
