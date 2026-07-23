from unittest.mock import AsyncMock, patch

import pytest

from bot.chat_sender import send_reply


@pytest.mark.asyncio
async def test_send_reply_success(mock_page):
    mock_input = AsyncMock()
    mock_input.evaluate.return_value = "input"
    mock_page.query_selector.return_value = mock_input
    
    with patch("bot.chat_sender.do_human_delay", new_callable=AsyncMock):
        result = await send_reply(mock_page, "Silakan order", "user123")
        assert result is True
        mock_input.fill.assert_called_once_with("Silakan order")
        mock_page.keyboard.press.assert_called_with("Enter")
        
@pytest.mark.asyncio
async def test_send_reply_no_input(mock_page):
    mock_page.query_selector.return_value = None
    mock_page.query_selector_all.return_value = []
    
    result = await send_reply(mock_page, "Silakan order", "user123")
    assert result is False
