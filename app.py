import os
import io
import tempfile
import wave
import base64
import threading
import uuid
import traceback
from flask import Flask, render_template, jsonify, request, g, redirect, url_for
import config

# Load .env explicitly if needed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from utils.logger import setup_logging, get_logger

# Import engines safely
try:
    from voice.tts import TextToSpeechEngine, DummyTTS
except Exception:
    TextToSpeechEngine = None
    DummyTTS = None

try:
    from voice.stt import SpeechToTextEngine
except Exception:
    SpeechToTextEngine = None

try:
    from voice.ai_chat import AIChatEngine
except Exception:
    AIChatEngine = None

from core.guardian import GuardianCore, AlertService
from services.esp32 import ESP32Client
from core.state import SharedState
import auth

# ============================================================
# Setup
# ============================================================
setup_logging()
logger = get_logger("app")
app = Flask(__name__)

# Session secret. Falls back to a random key if SECRET_KEY is not set in .env
# (a random key logs everyone out on restart — set SECRET_KEY in .env for stable sessions).
app.secret_key = config.SECRET_KEY or os.urandom(32)
if not config.SECRET_KEY:
    logger.warning("SECRET_KEY not set in .env — using a random key (sessions reset on restart)")
if not config.ADMIN_PASSWORD_HASH:
    logger.warning("ADMIN_PASSWORD_HASH not set in .env — all logins will be rejected")

shared_state = SharedState()

# Services Initialization
alert_service = AlertService(shared_state)
esp32_client = ESP32Client(config.ESP32_BASE_URL)

try:
    stt_engine = SpeechToTextEngine(config)
except Exception as e:
    print(f"STT init error: {e}")
    stt_engine = None

try:
    tts_engine = TextToSpeechEngine(config)
except Exception as e:
    print(f"TTS init error: {e}")
    tts_engine = None

try:
    ai_chat = AIChatEngine(config)
except Exception as e:
    print(f"AI Chat init error: {e}")
    ai_chat = None

if not tts_engine or (hasattr(tts_engine, 'client') and not tts_engine.client):
    if DummyTTS:
        tts_engine = DummyTTS()

core = GuardianCore(
    config=config,
    shared_state=shared_state,
    alert_service=alert_service,
    esp32_client=esp32_client,
    stt_engine=stt_engine,
    tts_engine=tts_engine,
    ai_chat=ai_chat,
)

class GuardianRunner:
    def __init__(self, core_obj):
        self.core = core_obj
        self.running = False

    def start(self):
        if self.running: return {"ok": True, "message": "Already running"}
        self.running = True
        t = threading.Thread(target=self.core.run, daemon=True)
        t.start()
        return {"ok": True, "message": "Guardian started"}

    def stop(self):
        self.core.stop()
        self.running = False
        return {"ok": True, "message": "Guardian stopped"}

guardian_runner = GuardianRunner(core)

# ============================================================
# Auth guard — protects the dashboard and every /api route
# ============================================================

@app.before_request
def require_login():
    if request.endpoint in auth.PUBLIC_ENDPOINTS:
        return None
    if auth.is_logged_in():
        return None
    return auth._auth_failed_response()


@app.route("/login", methods=["GET", "POST"])
def login():
    if auth.is_logged_in():
        return redirect(url_for("index"))

    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if auth.verify_credentials(username, password):
            auth.login_user()
            logger.info("Admin login successful")
            next_url = request.args.get("next") or url_for("index")
            # Only allow local redirects
            if not next_url.startswith("/"):
                next_url = url_for("index")
            return redirect(next_url)
        error = "نام کاربری یا رمز عبور اشتباه است"
        logger.warning("Failed login attempt")

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    auth.logout_user()
    return redirect(url_for("login"))


# ============================================================
# Routes
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/data")
def api_data():
    """Returns sensor data safely, even if attributes are missing."""
    data = {
        "temperature": 0,
        "mq9": 0,
        "flame": False,
        "motion": False,
        "door": False,
        "alarm_status": False,
        "online": False,
        "error": ""
    }
    
    try:
        if esp32_client:
            raw = esp32_client.get_data()
            if isinstance(raw, dict) and not raw.get("error"):
                data.update({
                    "temperature": raw.get("temperature", 0),
                    "mq9": raw.get("mq9", 0),
                    "flame": raw.get("flame", False),
                    "motion": raw.get("motion", False),
                    "door": raw.get("door", False),
                    "alarm_status": raw.get("alarm", False),
                    "online": True
                })
            else:
                data["error"] = str(raw.get("error", "ESP32 Offline")) if isinstance(raw, dict) else "ESP32 Offline"
    except Exception as e:
        logger.error(f"Error in api_data esp32 fetch: {e}")
        data["error"] = str(e)

    try:
        shared_state.set_sensor_data(data)
    except Exception:
        pass
    
    # Safe attribute access
    status = {
        "guardian_status": bool(getattr(guardian_runner, 'running', False)),
        "alarm_muted": bool(shared_state.get_alarm_muted()),
        "stt_active": bool(getattr(shared_state, 'stt_active', False)),
        "tts_active": bool(tts_engine.is_enabled()) if (tts_engine and hasattr(tts_engine, 'is_enabled')) else False,
        "last_voice_command": str(shared_state.get_last_command()),
        "last_response": str(shared_state.get_last_response())
    }
    return jsonify({"ok": True, **data, **status})

@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json() or {}
        text = data.get("text") or data.get("command")
        if not text: return jsonify({"ok": False, "error": "No text"}), 400

        shared_state.set_last_command(text)
        reply = core.chat(text, speak=False)
        shared_state.set_last_response(reply)

        audio_b64 = None
        if tts_engine and hasattr(tts_engine, 'get_audio_base64'):
            audio_b64 = tts_engine.get_audio_base64(reply)

        return jsonify({"ok": True, "reply": reply, "audio": audio_b64})
    except Exception as e:
        logger.exception("Chat API error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/voice/transcribe", methods=["POST"])
def api_voice():
    if not stt_engine or not hasattr(stt_engine, 'client') or not stt_engine.client:
        return jsonify({"ok": False, "error": "STT not configured"}), 503

    audio_file = request.files.get("audio")
    if not audio_file: return jsonify({"ok": False, "error": "No audio"}), 400

    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            audio_file.save(tmp.name)
            tmp_path = tmp.name

        with open(tmp_path, "rb") as f:
            transcript = stt_engine.client.audio.transcriptions.create(
                model=stt_engine.model,
                file=f,
                language="fa"
            )
        heard = transcript.text.strip()
        os.remove(tmp_path)

        if not heard: return jsonify({"ok": True, "heard": "", "reply": ""})

        shared_state.set_last_command(heard)
        reply = core.chat(heard, speak=False)
        shared_state.set_last_response(reply)
        
        audio_b64 = tts_engine.get_audio_base64(reply) if (tts_engine and hasattr(tts_engine, 'get_audio_base64')) else None

        return jsonify({"ok": True, "heard": heard, "reply": reply, "audio": audio_b64})
    except Exception as e:
        logger.exception("Voice API error")
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/guardian/start", methods=["POST"])
def start_g(): return jsonify(guardian_runner.start())

@app.route("/api/guardian/stop", methods=["POST"])
def stop_g(): return jsonify(guardian_runner.stop())

@app.route("/api/alarm/mute", methods=["POST"])
def mute():
    shared_state.set_alarm_muted(True)
    return jsonify({"ok": True})

@app.route("/api/alarm/unmute", methods=["POST"])
def unmute():
    shared_state.set_alarm_muted(False)
    return jsonify({"ok": True})

@app.route("/api/stt/start", methods=["POST"])
def stt_on():
    shared_state.stt_active = True
    return jsonify({"ok": True})

@app.route("/api/stt/stop", methods=["POST"])
def stt_off():
    shared_state.stt_active = False
    return jsonify({"ok": True})

@app.route("/api/config", methods=["GET", "POST"])
def config_api():
    if request.method == "GET":
        return jsonify({"ok": True, "config": {"ESP32_IP": config.ESP32_IP}})
    return jsonify({"ok": True})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
