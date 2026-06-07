import os
import io
import json
import time
import tempfile
import wave
import base64
import threading
import uuid
import traceback
from flask import (
    Flask, render_template, jsonify, request, g, redirect, url_for,
    Response, stream_with_context,
)
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

# Seed runtime-adjustable settings from config (changeable later from dashboard).
shared_state.update_settings({
    "STT_LANGUAGE": getattr(config, "STT_LANGUAGE", "fa"),
    "SYSTEM_PROMPT": getattr(config, "SYSTEM_PROMPT", ""),
    "ESP32_IP": getattr(config, "ESP32_BASE_URL", getattr(config, "ESP32_IP", "")),
})

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
# Sensor payload + real-time background poller
# ============================================================

def build_dashboard_payload(raw):
    """
    Normalize raw ESP32 data into the dashboard/SSE payload.
    The alarm decision and per-reason flags come straight from the hardware
    (raw['alarm'] / raw['alarm_reasons']); the backend only overlays the
    server-side mute state.
    """
    raw = raw or {}
    online = bool(raw.get("esp32_online", not raw.get("error")))
    door = raw.get("door", raw.get("door_open", False))
    gas = raw.get("mq9", raw.get("gas", 0))

    tts_active = False
    if tts_engine is not None:
        tts_active = bool(tts_engine.is_enabled()) if hasattr(tts_engine, "is_enabled") else True

    return {
        "ok": True,
        "online": online,
        "esp32_connected": online,
        "esp32_base_url": raw.get("esp32_base_url", getattr(esp32_client, "base_url", "")),
        "esp32_error": raw.get("esp32_last_error", raw.get("error", "")),
        "esp32_error_detail": raw.get("esp32_last_error_detail", ""),

        # Sensors (alarm decision is the hardware's)
        "temperature": raw.get("temperature", 0),
        "mq9": gas,
        "gas": gas,
        "flame": bool(raw.get("flame", False)),
        "motion": bool(raw.get("motion", False)),
        "door": bool(door),
        "alarm_status": bool(raw.get("alarm", False)),
        "alarm_reasons": raw.get("alarm_reasons", {}),
        "warmup_done": bool(raw.get("warmup_done", True)),
        "scenario_phase": raw.get("scenario_phase", ""),
        "device_name": raw.get("device_name", ""),

        # System status
        "guardian_status": bool(getattr(guardian_runner, "running", False)),
        "alarm_muted": bool(shared_state.get_alarm_muted()),
        "stt_active": bool(getattr(shared_state, "stt_active", False)),
        "tts_active": tts_active,
        "last_voice_command": str(shared_state.get_last_command()),
        "last_response": str(shared_state.get_last_response()),
    }


def get_latest_sensor_payload():
    """Return the freshest dashboard payload, using the poller snapshot if available."""
    with shared_state.current_data_lock:
        raw = dict(shared_state.current_data or {})
    if not raw:
        # No snapshot yet (poller not running) — fetch directly.
        try:
            raw = esp32_client.get_data()
            with shared_state.current_data_lock:
                shared_state.current_data = raw
        except Exception as e:
            logger.debug(f"Direct sensor fetch failed: {e}")
            raw = {}
    return build_dashboard_payload(raw)


_poller_started = False
_poller_lock = threading.Lock()


def _sensor_poller():
    """Background loop: poll ESP32 ~1s so SSE/dashboard stay live regardless of client count."""
    logger.info("Sensor poller started")
    while True:
        try:
            raw = esp32_client.get_data()
            with shared_state.current_data_lock:
                shared_state.current_data = raw
        except Exception as e:
            logger.debug(f"Sensor poller error: {e}")
        time.sleep(1)


def start_background_threads():
    global _poller_started
    with _poller_lock:
        if _poller_started:
            return
        _poller_started = True
        t = threading.Thread(target=_sensor_poller, daemon=True)
        t.start()


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
    """One-shot sensor snapshot (used as SSE fallback and by external callers)."""
    try:
        # Direct fetch keeps this endpoint correct even without the poller running.
        raw = esp32_client.get_data() if esp32_client else {}
        with shared_state.current_data_lock:
            shared_state.current_data = raw
    except Exception as e:
        logger.error(f"Error in api_data esp32 fetch: {e}")
        raw = {"error": str(e), "esp32_online": False}

    payload = build_dashboard_payload(raw)
    try:
        shared_state.set_sensor_data(payload)
    except Exception:
        pass
    return jsonify(payload)


@app.route("/api/stream")
def api_stream():
    """Server-Sent Events: pushes the latest sensor payload ~2x/second (real-time)."""
    start_background_threads()

    @stream_with_context
    def event_gen():
        # Send an immediate first frame so the UI fills instantly.
        while True:
            try:
                payload = get_latest_sensor_payload()
                yield f"data: {json.dumps(payload)}\n\n"
            except Exception as e:
                logger.debug(f"SSE frame error: {e}")
                yield f"data: {json.dumps({'ok': False, 'error': str(e)})}\n\n"
            time.sleep(0.5)

    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

@app.route("/api/chat", methods=["POST"])
def api_chat():
    try:
        data = request.get_json() or {}
        text = data.get("text") or data.get("command")
        if not text: return jsonify({"ok": False, "error": "No text"}), 400

        shared_state.set_last_command(text)
        reply = core.chat(text, speak=False,
                          system_prompt=shared_state.get_setting("SYSTEM_PROMPT") or None)
        shared_state.set_last_response(reply)

        audio_b64 = None
        if tts_engine and hasattr(tts_engine, 'get_audio_base64'):
            audio_b64 = tts_engine.get_audio_base64(reply)

        return jsonify({"ok": True, "reply": reply, "response": reply, "audio": audio_b64})
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

        stt_language = shared_state.get_setting("STT_LANGUAGE", "fa") or "fa"
        with open(tmp_path, "rb") as f:
            transcript = stt_engine.client.audio.transcriptions.create(
                model=stt_engine.model,
                file=f,
                language=stt_language
            )
        heard = transcript.text.strip()
        os.remove(tmp_path)

        if not heard: return jsonify({"ok": True, "heard": "", "reply": ""})

        shared_state.set_last_command(heard)
        reply = core.chat(heard, speak=False,
                          system_prompt=shared_state.get_setting("SYSTEM_PROMPT") or None)
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
    return jsonify({"ok": True, "muted": True})

@app.route("/api/alarm/unmute", methods=["POST"])
def unmute():
    shared_state.set_alarm_muted(False)
    return jsonify({"ok": True, "muted": False})

@app.route("/api/stt/start", methods=["POST"])
def stt_on():
    shared_state.stt_active = True
    return jsonify({"ok": True})

@app.route("/api/stt/stop", methods=["POST"])
def stt_off():
    shared_state.stt_active = False
    return jsonify({"ok": True})

# Keys the ESP32 hardware understands (POST /config).
HARDWARE_CONFIG_KEYS = [
    "gas_threshold", "temp_threshold",
    "flame_enabled", "motion_enabled", "door_enabled",
    "device_name", "read_interval",
]
# Accept the dashboard's UPPER_CASE names and map them to hardware keys.
CONFIG_KEY_ALIASES = {
    "GAS_THRESHOLD": "gas_threshold",
    "TEMPERATURE_THRESHOLD": "temp_threshold",
    "TEMP_THRESHOLD": "temp_threshold",
}


@app.route("/api/config", methods=["GET", "POST"])
def config_api():
    """
    GET  -> thresholds read live from the ESP32 hardware + app-level settings.
    POST -> threshold keys are written to the hardware (POST /config);
            app-level keys (ESP32_IP / STT_LANGUAGE / SYSTEM_PROMPT) applied at runtime.
    """
    if request.method == "GET":
        hw = esp32_client.get_config() if esp32_client else {}
        settings = shared_state.get_settings()
        cfg = {
            "gas_threshold": hw.get("gas_threshold"),
            "temp_threshold": hw.get("temp_threshold"),
            "flame_enabled": hw.get("flame_enabled"),
            "motion_enabled": hw.get("motion_enabled"),
            "door_enabled": hw.get("door_enabled"),
            "device_name": hw.get("device_name"),
            "read_interval": hw.get("read_interval"),
            "esp32_online": bool(hw.get("esp32_online", False)),
            "ESP32_IP": settings.get("ESP32_IP") or getattr(esp32_client, "base_url", ""),
            "STT_LANGUAGE": settings.get("STT_LANGUAGE", "fa"),
            "SYSTEM_PROMPT": settings.get("SYSTEM_PROMPT", ""),
        }
        return jsonify({"ok": True, "config": cfg, "esp32_online": cfg["esp32_online"]})

    # POST
    data = request.get_json(silent=True) or {}
    result = {"ok": True}

    # 1) Hardware threshold keys -> forward to ESP32
    hw_payload = {}
    for key, value in data.items():
        mapped = CONFIG_KEY_ALIASES.get(key, key)
        if mapped in HARDWARE_CONFIG_KEYS:
            hw_payload[mapped] = value
    if hw_payload:
        hw_resp = esp32_client.send_config(hw_payload)
        result["esp32"] = hw_resp
        if isinstance(hw_resp, dict) and hw_resp.get("success") is False:
            result["ok"] = False
            result["error"] = hw_resp.get("error", "ESP32 config write failed")

    # 2) App-level settings -> apply at runtime
    app_settings = {}
    if data.get("ESP32_IP"):
        new_url = esp32_client.set_base_url(data["ESP32_IP"])
        app_settings["ESP32_IP"] = data["ESP32_IP"]
        result["esp32_base_url"] = new_url
    if data.get("STT_LANGUAGE"):
        app_settings["STT_LANGUAGE"] = data["STT_LANGUAGE"]
    if "SYSTEM_PROMPT" in data:
        app_settings["SYSTEM_PROMPT"] = data["SYSTEM_PROMPT"]
    if app_settings:
        shared_state.update_settings(app_settings)

    result["status"] = "تنظیمات ذخیره شد" if result["ok"] else "ذخیره تنظیمات سخت‌افزار ناموفق بود"
    return jsonify(result), (200 if result["ok"] else 502)


if __name__ == "__main__":
    start_background_threads()
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)
