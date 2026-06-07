"""Tests for the reworked /api/data (hardware passthrough) and /api/config proxy."""


class FakeESP32:
    base_url = "http://sim"

    def __init__(self, data=None, config=None):
        self._data = data or {}
        self._config = config or {}
        self.sent = None
        self.new_ip = None

    def get_data(self):
        return dict(self._data)

    def get_config(self):
        return dict(self._config)

    def send_config(self, payload):
        self.sent = dict(payload)
        return {"success": True, "config": payload}

    def set_base_url(self, ip):
        self.new_ip = ip
        self.base_url = ip if ip.startswith("http") else f"http://{ip}:80"
        return self.base_url


def test_api_data_passes_through_hardware_alarm(client, monkeypatch):
    import app as app_module

    fake = FakeESP32(data={
        "mq9": 2600, "temperature": 25, "flame": False, "motion": False,
        "door_open": False, "alarm": True,
        "alarm_reasons": {"gas_alarm": True, "temp_alarm": False, "flame_alarm": False,
                          "motion_alarm": False, "door_alarm": False},
        "warmup_done": True, "esp32_online": True,
    })
    monkeypatch.setattr(app_module, "esp32_client", fake)

    r = client.get("/api/data")
    assert r.status_code == 200
    d = r.get_json()
    assert d["online"] is True
    assert d["alarm_status"] is True
    assert d["alarm_reasons"]["gas_alarm"] is True
    assert d["mq9"] == 2600
    assert d["warmup_done"] is True
    assert d["door"] is False  # normalized from door_open


def test_api_data_mute_overlay(client, monkeypatch):
    import app as app_module
    fake = FakeESP32(data={"mq9": 100, "alarm": False, "esp32_online": True})
    monkeypatch.setattr(app_module, "esp32_client", fake)

    client.post("/api/alarm/mute")
    d = client.get("/api/data").get_json()
    assert d["alarm_muted"] is True
    client.post("/api/alarm/unmute")


def test_config_get_reads_from_hardware(client, monkeypatch):
    import app as app_module
    fake = FakeESP32(config={
        "gas_threshold": 2000, "temp_threshold": 50, "flame_enabled": True,
        "motion_enabled": True, "door_enabled": True, "device_name": "X",
        "read_interval": 2, "esp32_online": True,
    })
    monkeypatch.setattr(app_module, "esp32_client", fake)

    r = client.get("/api/config")
    assert r.status_code == 200
    cfg = r.get_json()["config"]
    assert cfg["gas_threshold"] == 2000
    assert cfg["temp_threshold"] == 50
    assert cfg["esp32_online"] is True
    assert "STT_LANGUAGE" in cfg


def test_config_post_writes_threshold_to_hardware(client, monkeypatch):
    import app as app_module
    fake = FakeESP32()
    monkeypatch.setattr(app_module, "esp32_client", fake)

    r = client.post("/api/config", json={"GAS_THRESHOLD": 3500, "TEMPERATURE_THRESHOLD": 55,
                                         "STT_LANGUAGE": "en", "SYSTEM_PROMPT": "test prompt"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    # Hardware received mapped keys
    assert fake.sent["gas_threshold"] == 3500
    assert fake.sent["temp_threshold"] == 55
    # App-level settings applied at runtime
    assert app_module.shared_state.get_setting("STT_LANGUAGE") == "en"
    assert app_module.shared_state.get_setting("SYSTEM_PROMPT") == "test prompt"


def test_config_post_switches_esp32_ip(client, monkeypatch):
    import app as app_module
    fake = FakeESP32()
    monkeypatch.setattr(app_module, "esp32_client", fake)

    r = client.post("/api/config", json={"ESP32_IP": "http://127.0.0.1:8080"})
    assert r.status_code == 200
    assert fake.new_ip == "http://127.0.0.1:8080"
    assert app_module.shared_state.get_setting("ESP32_IP") == "http://127.0.0.1:8080"
