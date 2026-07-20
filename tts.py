"""ElevenLabs TTS: per-juror voice assignment, generate and cache audio."""

import base64
import hashlib
import os
from functools import lru_cache
from pathlib import Path

import httpx

CACHE_DIR = Path(".cache/tts")

API_KEY = os.environ.get("ELEVENLABS_API_KEY",
                          "172f46b7e9348768ff36d41aeb0073204065c59419c5548f38e4367b038feae6")
BASE = "https://api.elevenlabs.io/v1"
MODEL_ID = "eleven_v3"


class MissingAPIKey(Exception):
    """ELEVENLABS_API_KEY is not set."""

JUROR_VOICES = {
    1:  "onwK4e9ZLuTAKqWW03F9",   # Daniel   – steady broadcaster: orderly foreman
    2:  "bIHbv24MWmeRgasZH58o",   # Will     – relaxed, soft: meek bank teller
    3:  "pNInz6obpgDQGcFmaJgB",   # Adam     – dominant, firm: loud, domineering
    4:  "cjVigY5qzO86Huf0OWal",   # Eric     – smooth, classy: cool stockbroker
    5:  "UgBBYS2sOqTuMpoF3BR0",   # Mark     – natural, casual: quiet orderly
    6:  "iP95p4xoKVk53GoZ742B",   # Chris    – down-to-earth: house painter
    7:  "IKne3meq5aSn9XLyUdCD",   # Charlie  – energetic, hyped: wisecracking salesman
    8:  "nPczCjzI2devNBz1zQrb",   # Brian    – deep, comforting: thoughtful architect
    9:  "pqHfZKP75CvOlQylNhV4",   # Bill     – wise, old: gentle retiree
    10: "BDqe1qZiwPi7xspRxTgh",   # Roderich – crude, grumpy: coarse garage owner
    11: "JBFqnCBsd6RMkjVDRZzb",   # George   – warm, mature: courteous watchmaker
    12: "CwhRBWXzGAHq8TQ4Fs17",   # Roger    – laid-back, classy: glib ad exec
}

DEFAULT_VOICE = "pNInz6obpgDQGcFmaJgB"

# Court announcer / case reader – neutral, calm, informative
NARRATOR_VOICE = "SAz9YHcvj6GT2YYXdXww"   # River


def voice_for(seat: int) -> str:
    return JUROR_VOICES.get(seat, DEFAULT_VOICE)


@lru_cache(maxsize=256)
def _cached(text: str, voice_id: str) -> str:
    """Generate TTS audio, return base64-encoded MP3 data URL.

    Cached in memory per process and on disk across restarts."""
    key = hashlib.sha256(f"{MODEL_ID}:{voice_id}:{text}".encode()).hexdigest()
    disk = CACHE_DIR / f"{key}.mp3.b64"
    if disk.exists():
        return f"data:audio/mpeg;base64,{disk.read_text()}"
    if not API_KEY:
        raise MissingAPIKey("set ELEVENLABS_API_KEY to enable voice audio")
    url = f"{BASE}/text-to-speech/{voice_id}"
    headers = {"xi-api-key": API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": MODEL_ID,
        "voice_settings": {"stability": 0.35, "similarity_boost": 0.75},
    }
    with httpx.Client(timeout=60) as client:
        resp = client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        b64 = base64.b64encode(resp.content).decode("ascii")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    disk.write_text(b64)
    return f"data:audio/mpeg;base64,{b64}"


def generate(text: str, seat: int) -> str:
    """Return a data URL of MP3 audio for *text* in *seat*'s voice."""
    return _cached(text, voice_for(seat))


def _strip_markdown(text: str) -> str:
    """Markdown headings/emphasis read badly aloud; drop the markers."""
    lines = []
    for line in text.splitlines():
        lines.append(line.lstrip("#").strip().replace("**", "").replace("*", ""))
    return "\n".join(lines)


def narrate(text: str) -> str:
    """Return a data URL of MP3 audio for *text* in the announcer's voice."""
    return _cached(_strip_markdown(text)[:4500], NARRATOR_VOICE)
