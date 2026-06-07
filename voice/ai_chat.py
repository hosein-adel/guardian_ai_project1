import json
import os
from openai import OpenAI

class AIChatEngine:
    def __init__(self, config=None):
        self.config = config
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.base_url = os.getenv("OPENAI_BASE_URL", "https://api.gapgpt.app/v1")
        self.model = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)
            print(f"[AIChatEngine] Initialized: {self.model}")
        else:
            self.client = None
            print("[AIChatEngine] No API Key found!")

    def chat(self, user_text: str, sensor_context: dict = None, raise_errors: bool = False,
             system_prompt: str = None) -> str:
        if not self.client: return "کلید API تنظیم نشده است."

        # Runtime system prompt (from dashboard settings) overrides the default.
        if not system_prompt:
            system_prompt = getattr(self.config, "SYSTEM_PROMPT", "") if self.config else ""
        if not system_prompt:
            system_prompt = "You are Guardian AI. Answer concisely in Persian."

        messages = [{"role": "system", "content": system_prompt}]
        if sensor_context:
            messages.append({"role": "system", "content": f"Sensor Data: {json.dumps(sensor_context)}"})
        messages.append({"role": "user", "content": user_text})

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[AIChatEngine] Chat Error: {e}")
            return f"خطا در چت: {str(e)}"

    def summarize_alarms(self, reasons: list, lang: str = "fa") -> str:
        """این متد برای حل خطای لاگ شما اضافه شد"""
        msg = f"هشدار! موارد روبرو شناسایی شدند: {', '.join(reasons)}"
        if not self.client: return msg
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": f"خلاصه وار و فوری به فارسی بگو که این آلارم‌ها رخ داده: {reasons}"}],
                max_tokens=100
            )
            return response.choices[0].message.content.strip()
        except:
            return msg
