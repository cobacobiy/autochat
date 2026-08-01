from bot.knowledge import parse_knowledge_answers
from bot.state import bot_state


def test_parse_knowledge_answers():
    bot_state.knowledge_base = "T: bisa cod?\nJ: Ya, bisa kak\nT: harga grosir?\nJ: Cek etalase kak"
    parse_knowledge_answers()
    
    assert "bisa cod?" in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["bisa cod?"] == "Ya, bisa kak"
    assert "harga grosir?" in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["harga grosir?"] == "Cek etalase kak"

def test_parse_knowledge_answers_ignore_comments():
    bot_state.knowledge_base = "# Ini komentar\n#T: bisa cod?\n#J: Ya, bisa\nT: bisa cod?\nJ: Tentu"
    parse_knowledge_answers()
    
    assert "#t: bisa cod?" not in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["bisa cod?"] == "Tentu"

def test_parse_knowledge_answers_multiline():
    bot_state.knowledge_base = "T: gausah lah kak\nkalo order ulang kelamaan nanti\nJ: bisa di order ulang kak"
    parse_knowledge_answers()
    
    assert "gausah lah kak kalo order ulang kelamaan nanti" in bot_state.knowledge_answers
    assert bot_state.knowledge_answers["gausah lah kak kalo order ulang kelamaan nanti"] == "bisa di order ulang kak"
