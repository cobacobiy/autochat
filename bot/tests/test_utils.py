from bot.main import _clean_ai_reply, get_auto_reply, is_assistant_ai_msg

def test_clean_ai_reply():
    assert _clean_ai_reply("J: Ini jawabannya") == "Ini jawabannya"
    assert _clean_ai_reply("j : Tentu kak") == "Tentu kak"
    assert _clean_ai_reply("Anda: Halo") == "Halo"
    assert _clean_ai_reply("Jawaban normal") == "Jawaban normal"
    assert _clean_ai_reply("  J: Trim spasi   ") == "Trim spasi"

def test_get_auto_reply():
    # Matches whole word only
    assert get_auto_reply("berapa harga nya?") == "Harga sudah tertera di halaman produk. Silakan cek ya kak 😊"
    assert get_auto_reply("bisa cod kak?") == "Maaf, belum tersedia COD untuk saat ini."
    
    # Should not match substrings
    assert get_auto_reply("tolong kasih barcode") == "TIDAK TAHU" # "cod" inside "barcode"
    assert get_auto_reply("barang ga sesuai") == "TIDAK TAHU"

def test_is_assistant_ai_msg():
    # Should return True for AI assistant messages
    assert is_assistant_ai_msg("Asisten AI Toko\nHalo")
    assert is_assistant_ai_msg("[Asisten AI] Bisa dibantu")
    assert not is_assistant_ai_msg("Pesan otomatis: Hai") # Note: We don't have "Pesan otomatis" in the keyword list of the actual code
    
    # Should return False for normal messages
    assert not is_assistant_ai_msg("Pembeli: Halo min")
    assert not is_assistant_ai_msg("Berapa harganya?")
