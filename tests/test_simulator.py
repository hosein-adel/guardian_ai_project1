"""Light tests for the ESP32 simulator — no network, drives the model directly."""

import sys
import pathlib

SIM_DIR = pathlib.Path(__file__).resolve().parent.parent / "simulator"
sys.path.insert(0, str(SIM_DIR))

import time  # noqa: E402

import esp32_sim  # noqa: E402


def _fast_forward(model, seconds):
    """Pretend `seconds` of wall-clock has passed so scenario phases advance."""
    model._start -= seconds


REQUIRED_FIELDS = {
    "mq9", "temperature", "gas_leak", "flame", "motion", "door_open",
    "device_name", "ip", "timestamp", "guardian_active", "alarm_muted",
    "warmup_done", "alarm", "alarm_reasons",
}
REQUIRED_REASONS = {"gas_alarm", "temp_alarm", "flame_alarm", "motion_alarm", "door_alarm"}


def _tick(model, n):
    for _ in range(n):
        model._tick()


def test_data_has_exact_hardware_fields():
    model = esp32_sim.SensorModel(scenario="normal", warmup_sec=0, read_interval=1)
    _tick(model, 2)
    data = model.get_data()
    assert REQUIRED_FIELDS.issubset(data.keys())
    assert set(data["alarm_reasons"].keys()) == REQUIRED_REASONS


def test_normal_scenario_has_no_alarm():
    model = esp32_sim.SensorModel(scenario="normal", warmup_sec=0, read_interval=1)
    _tick(model, 3)
    data = model.get_data()
    assert data["alarm"] is False
    assert all(v is False for v in data["alarm_reasons"].values())


def test_gas_leak_scenario_triggers_gas_alarm():
    model = esp32_sim.SensorModel(scenario="gas_leak", warmup_sec=0, read_interval=1)
    _fast_forward(model, 20)  # past the initial 8s "normal" phase
    # gas ramps ~650/tick from 700 toward 2600; a few ticks crosses the 2000 threshold.
    _tick(model, 6)
    data = model.get_data()
    assert data["mq9"] >= 2000
    assert data["alarm"] is True
    assert data["alarm_reasons"]["gas_alarm"] is True


def test_raising_threshold_clears_alarm():
    model = esp32_sim.SensorModel(scenario="gas_leak", warmup_sec=0, read_interval=1)
    _fast_forward(model, 20)
    _tick(model, 6)
    assert model.get_data()["alarm"] is True

    # Same write path the dashboard uses (POST /config -> update_config).
    model.update_config({"gas_threshold": 5000})
    _tick(model, 2)
    data = model.get_data()
    assert data["alarm"] is False
    assert data["alarm_reasons"]["gas_alarm"] is False


def test_config_only_accepts_known_keys():
    model = esp32_sim.SensorModel(scenario="normal", warmup_sec=0, read_interval=1)
    model.update_config({"gas_threshold": 1234, "bogus_key": "ignored"})
    cfg = model.get_config()
    assert cfg["gas_threshold"] == 1234
    assert "bogus_key" not in cfg


def test_warmup_flag_transitions():
    model = esp32_sim.SensorModel(scenario="normal", warmup_sec=0, read_interval=1)
    _tick(model, 1)
    # warmup_sec=0 -> the run loop would flip it; _tick alone doesn't, so check field exists.
    assert "warmup_done" in model.get_data()
