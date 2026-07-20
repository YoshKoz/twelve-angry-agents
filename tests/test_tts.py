from unittest.mock import patch

import pytest

import tts


def test_missing_api_key_raises_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "API_KEY", "")
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    tts._cached.cache_clear()
    with pytest.raises(tts.MissingAPIKey):
        tts.generate("never cached, no key", 1)


def test_disk_cache_hit_skips_api_key_check(tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "API_KEY", "")
    monkeypatch.setattr(tts, "CACHE_DIR", tmp_path)
    tts._cached.cache_clear()
    import hashlib
    voice_id = tts.voice_for(1)
    key = hashlib.sha256(f"{tts.MODEL_ID}:{voice_id}:hello".encode()).hexdigest()
    (tmp_path / f"{key}.mp3.b64").write_text("Zm9v")
    with patch("httpx.Client.post") as mock_post:
        url = tts.generate("hello", 1)
    mock_post.assert_not_called()
    assert url == "data:audio/mpeg;base64,Zm9v"
