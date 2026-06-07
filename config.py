import os
from dotenv import load_dotenv

# لود کردن فایل .env
load_dotenv()

# Flask
FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
FLASK_DEBUG = os.getenv("FLASK_DEBUG", "true").lower() == "true"

# Auth (single admin role)
SECRET_KEY = os.getenv("SECRET_KEY", "")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
# Hashed password (werkzeug pbkdf2 format). Generate with: python tools/gen_password.py
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH", "")

# OpenAI / GapGPT
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
OPENAI_CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_TTS_VOICE = os.getenv("OPENAI_TTS_VOICE", "shimmer")
OPENAI_STT_MODEL = os.getenv("OPENAI_STT_MODEL", "whisper-1")

# Default language sent to Whisper for speech-to-text. Can be changed at
# runtime from the dashboard settings (stored in SharedState).
STT_LANGUAGE = os.getenv("STT_LANGUAGE", "fa")


def load_system_prompt(default=""):
    """Read the initial AI system prompt from prompts/system.txt if present."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "system.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return default


SYSTEM_PROMPT = load_system_prompt()

# ESP32
ESP32_IP = os.getenv("ESP32_IP", "192.168.43.219")
ESP32_BASE_URL = os.getenv("ESP32_BASE_URL", f"http://{ESP32_IP}")
