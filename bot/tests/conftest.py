import pytest
from unittest.mock import AsyncMock

@pytest.fixture
def mock_page():
    page = AsyncMock()
    page.content.return_value = "<html></html>"
    page.evaluate.return_value = "test"
    page.query_selector.return_value = AsyncMock()
    page.query_selector_all.return_value = []
    return page
