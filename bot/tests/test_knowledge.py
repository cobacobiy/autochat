from bot.knowledge import parse_knowledge_answers
from bot.state import bot_state

def test_parse_knowledge_answers():
    bot_state.knowledge_base = "bisa cod? | Ya, bisa kak\nharga grosir? | Cek etalase kak"
    parse_knowledge_answers()
    
    assert "bisa cod?" in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["bisa cod?"] == "Ya, bisa kak"
    assert "harga grosir?" in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["harga grosir?"] == "Cek etalase kak"

def test_parse_knowledge_answers_ignore_comments():
    bot_state.knowledge_base = "# Ini komentar\n#bisa cod? | Ya, bisa\nbisa cod? | Tentu"
    parse_knowledge_answers()
    
    assert "#bisa cod?" not in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["bisa cod?"] == "Tentu"
