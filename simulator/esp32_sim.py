"""
Guardian AI — ESP32 Hardware Simulator
=======================================

A standalone Flask service that mimics the real ESP32 (esp32/main.py) EXACTLY,
so the dashboard and backend can be tested without physical hardware.

It exposes the same HTTP API as the device:

    GET  /          -> 200 status page (used by ESP32Client.is_online())
    GET  /data      -> live sensor JSON (identical field names to the device)
    GET  /config    -> current config (thresholds etc.)
    POST /config    -> update config (only DEFAULT_CONFIG keys accepted)
    GET  /diag       -> tiny live diagnostics page
    OPTIONS *        -> CORS preflight

The sensor values are produced by a background thread driven by a *scenario*
(see scenarios.py). Analog values (mq9/temperature) ramp smoothly with noise;
boolean sensors (flame/motion/door) are fed through the SAME hysteresis state
machines the real firmware uses, so timing/behaviour matches the device.

Run:
    python simulator/esp32_sim.py --scenario demo
    python simulator/esp32_sim.py --scenario gas_leak --warmup 3 --port 8080

Then point the backend at it:
    ESP32_BASE_URL=http://127.0.0.1:8080   (in .env)

Switch back to real hardware later by setting ESP32_BASE_URL to the device IP —
no code changes needed.
"""

import os
import sys
import time
import json
import random
import argparse
import threading

from flask import Flask, request, Response

# Allow running both as "python simulator/esp32_sim.py" and as a module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import scenarios  # noqa: E402


# ============================================================
# Config — mirrors DEFAULT_CONFIG in esp32/main.py
# ============================================================
DEFAULT_CONFIG = {
    "gas_threshold": 2000,
    "temp_threshold": 50,
    "flame_enabled": True,
    "motion_enabled": True,
    "door_enabled": True,
    "device_name": "Guardian ESP32 (SIM)",
    "read_interval": 2,
}

# Hysteresis constants — identical to the firmware.
FLAME_ON = 4    # consecutive LOW reads to confirm flame ON
FLAME_OFF = 10  # consecutive HIGH reads to confirm flame OFF
PIR_CONFIRM = 1
PIR_RESET = 2


class SensorModel:
    """Background sensor simulator. Thread-safe snapshot via get_data()."""

    def __init__(self, scenario="demo", warmup_sec=30, read_interval=2, device_ip="127.0.0.1"):
        self.scenario = scenario
        self.warmup_sec = warmup_sec
        self.read_interval = read_interval
        self.device_ip = device_ip

        self.config = dict(DEFAULT_CONFIG)
        self.config["read_interval"] = read_interval

        self._lock = threading.RLock()
        self._start = time.time()
        self._warmup_done = False

        # Continuous (analog) current values — ramped toward setpoints.
        self._gas = float(scenarios.NORMAL["gas"])
        self._temp = float(scenarios.NORMAL["temp"])

        # Flame hysteresis state (mirrors firmware globals).
        self._fl_active = False
        self._fl_lo = 0
        self._fl_hi = 0

        # PIR state.
        self._pir_hi = 0
        self._pir_lo = 0

        self._last_data = {}
        self._running = True

    # -- config -------------------------------------------------
    def get_config(self):
        with self._lock:
            return dict(self.config)

    def update_config(self, new_config):
        with self._lock:
            for k in DEFAULT_CONFIG:
                if k in new_config:
                    self.config[k] = new_config[k]
            return dict(self.config)

    # -- analog ramp helper ------------------------------------
    @staticmethod
    def _ramp(current, target, max_step):
        delta = target - current
        if abs(delta) <= max_step:
            return target
        return current + max_step * (1 if delta > 0 else -1)

    # -- sensor reads (mirror firmware logic) ------------------
    def _read_flame(self, flame_present):
        """Active-LOW pin: flame present => raw=0. Same hysteresis as device."""
        raw = 0 if flame_present else 1
        if not self._fl_active:
            if raw == 0:
                self._fl_lo += 1
                if self._fl_lo >= FLAME_ON:
                    self._fl_active = True
                    self._fl_lo = 0
            else:
                self._fl_lo = 0
        else:
            if raw == 1:
                self._fl_hi += 1
                if self._fl_hi >= FLAME_OFF:
                    self._fl_active = False
                    self._fl_hi = 0
            else:
                self._fl_hi = 0
        return self._fl_active

    def _read_motion(self, motion_present):
        raw = 1 if motion_present else 0
        if raw == 1:
            self._pir_hi += 1
            self._pir_lo = 0
        else:
            self._pir_lo += 1
            if self._pir_lo >= PIR_RESET:
                self._pir_hi = 0
                self._pir_lo = 0
        return self._pir_hi >= PIR_CONFIRM

    def _evaluate_alarm(self, data):
        cfg = self.config
        gas_thr = cfg.get("gas_threshold", DEFAULT_CONFIG["gas_threshold"])
        temp_thr = cfg.get("temp_threshold", DEFAULT_CONFIG["temp_threshold"])
        reasons = {
            "gas_alarm": data["mq9"] >= gas_thr,
            "temp_alarm": (data["temperature"] is not None and data["temperature"] >= temp_thr),
            "flame_alarm": (cfg.get("flame_enabled", True) and data["flame"]),
            "motion_alarm": (cfg.get("motion_enabled", True) and data["motion"]),
            "door_alarm": (cfg.get("door_enabled", True) and data["door_open"]),
        }
        return any(reasons.values()), reasons

    # -- main loop ---------------------------------------------
    def _tick(self):
        elapsed = time.time() - self._start
        sp = scenarios.setpoints_at(self.scenario, elapsed)

        with self._lock:
            # Ramp analog values toward setpoints + sensor noise.
            self._gas = self._ramp(self._gas, sp["gas"], max_step=650)
            self._temp = self._ramp(self._temp, sp["temp"], max_step=9.0)

            mq9_val = int(max(0, min(4095, self._gas + random.uniform(-25, 25))))
            temp_val = round(self._temp + random.uniform(-0.25, 0.25), 2)

            flame_val = self._read_flame(sp["flame"])
            motion_val = self._read_motion(sp["motion"])
            door_val = bool(sp["door"])

            gas_thr = self.config.get("gas_threshold", DEFAULT_CONFIG["gas_threshold"])

            data = {
                "mq9": mq9_val,
                "temperature": temp_val,
                "gas_leak": mq9_val >= gas_thr,
                "flame": flame_val,
                "motion": motion_val,
                "door_open": door_val,
                "device_name": self.config.get("device_name", DEFAULT_CONFIG["device_name"]),
                "ip": self.device_ip,
                "timestamp": time.time(),
                "guardian_active": True,
                "alarm_muted": False,
                "warmup_done": self._warmup_done,
                # extra (sim-only, harmless): which scenario phase is active
                "scenario": self.scenario,
                "scenario_phase": sp.get("label", ""),
            }
            alarm, reasons = self._evaluate_alarm(data)
            data["alarm"] = alarm
            data["alarm_reasons"] = reasons
            self._last_data = data

    def get_data(self):
        with self._lock:
            if self._last_data:
                # refresh timestamp so clients see liveness
                self._last_data["timestamp"] = time.time()
                return dict(self._last_data)
        # Not ticked yet — produce one immediately.
        self._tick()
        with self._lock:
            return dict(self._last_data)

    def run(self):
        print(f"[SIM] scenario={self.scenario} warmup={self.warmup_sec}s interval={self.read_interval}s")
        # Warmup: device sleeps before marking warmup_done, but still serves data.
        warm_deadline = self._start + self.warmup_sec
        while self._running:
            self._tick()
            if not self._warmup_done and time.time() >= warm_deadline:
                self._warmup_done = True
                print("[SIM] MQ9 warm-up complete")
            time.sleep(self.read_interval)

    def stop(self):
        self._running = False


# ============================================================
# Flask app
# ============================================================
def create_app(model: SensorModel) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def add_cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    def jresp(payload, status=200):
        return Response(json.dumps(payload), status=status, mimetype="application/json")

    @app.route("/", methods=["GET"])
    def root():
        d = model.get_data()
        html = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<title>ESP32 Simulator</title>"
            "<meta http-equiv='refresh' content='2'>"
            "<style>body{background:#10212f;color:#cfe;font-family:monospace;padding:24px}"
            "h2{color:#4fc3f7}b{color:#ffd479}</style></head><body>"
            f"<h2>ESP32 Simulator — scenario: {d.get('scenario')}</h2>"
            f"<p>phase: <b>{d.get('scenario_phase')}</b> | "
            f"warmup_done: <b>{d.get('warmup_done')}</b> | alarm: <b>{d.get('alarm')}</b></p>"
            f"<pre>{json.dumps(d, ensure_ascii=False, indent=2)}</pre>"
            "<p><a style='color:#4fc3f7' href='/data'>/data</a> | "
            "<a style='color:#4fc3f7' href='/config'>/config</a> | "
            "<a style='color:#4fc3f7' href='/diag'>/diag</a></p>"
            "</body></html>"
        )
        return Response(html, mimetype="text/html")

    @app.route("/diag", methods=["GET"])
    def diag():
        return root()

    @app.route("/data", methods=["GET"])
    def data():
        return jresp(model.get_data())

    @app.route("/config", methods=["GET", "POST"])
    def config():
        if request.method == "GET":
            return jresp(model.get_config())
        try:
            body = request.get_json(force=True, silent=True)
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
            updated = model.update_config(body)
            return jresp({"success": True, "config": updated})
        except Exception as e:
            return jresp({"success": False, "error": str(e)}, status=400)

    @app.route("/<path:_any>", methods=["OPTIONS"])
    @app.route("/", methods=["OPTIONS"])
    def options(_any=None):
        return Response("", status=200)

    return app


def main():
    parser = argparse.ArgumentParser(description="ESP32 hardware simulator")
    parser.add_argument("--scenario", default=os.getenv("SIM_SCENARIO", "demo"),
                        choices=list(scenarios.SCENARIOS.keys()))
    parser.add_argument("--port", type=int, default=int(os.getenv("SIM_PORT", "8080")))
    parser.add_argument("--host", default=os.getenv("SIM_HOST", "0.0.0.0"))
    parser.add_argument("--warmup", type=int, default=int(os.getenv("SIM_WARMUP_SEC", "30")),
                        help="MQ9 warm-up seconds (use a small value like 3 for quick tests)")
    parser.add_argument("--interval", type=int, default=int(os.getenv("SIM_READ_INTERVAL", "2")),
                        help="sensor read interval seconds (device default is 2)")
    args = parser.parse_args()

    model = SensorModel(
        scenario=args.scenario,
        warmup_sec=args.warmup,
        read_interval=args.interval,
        device_ip="127.0.0.1",
    )
    t = threading.Thread(target=model.run, daemon=True)
    t.start()

    app = create_app(model)
    print(f"[SIM] ESP32 simulator on http://{args.host}:{args.port}  (scenario={args.scenario})")
    print(f"[SIM] point the backend at it:  ESP32_BASE_URL=http://127.0.0.1:{args.port}")
    # threaded=True so /data and /config stay responsive while the model ticks.
    app.run(host=args.host, port=args.port, threaded=True, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
