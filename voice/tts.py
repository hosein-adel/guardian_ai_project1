import os
import base64
import threading
from openai import OpenAI

class TextToSpeechEngine:
    def __init__(self, config=None):
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
        self.model = os.getenv("OPENAI_TTS_MODEL", "tts-1")
        self.voice = os.getenv("OPENAI_TTS_VOICE", "shimmer")
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        else:
            self.client = None

    def is_enabled(self):
        return self.client is not None

    def get_audio_base64(self, text):
        if not self.client or not text: return None
        try:
            response = self.client.audio.speech.create(
                model=self.model,
                voice=self.voice,
                input=str(text)
            )
            # گرفتن بایت‌های صدا
            audio_data = response.content
            return base64.b64encode(audio_data).decode("utf-8")
        except Exception as e:
            print(f"[TTS] Error generating audio: {e}")
            return None

    def speak(self, text):
        """فقط برای چاپ در کنسول اگر پخش مستقیم کار نکرد"""
        print(f"[TTS-Local] {text}")

class DummyTTS:
    def is_enabled(self): return False
    def get_audio_base64(self, text): return None
    def speak(self, text): print(f"[DummyTTS] {text}")
