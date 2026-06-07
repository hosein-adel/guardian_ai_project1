"""
Scenario definitions for the ESP32 simulator.

Each scenario is a list of "phases". A phase describes the *physical* state of
the sensors for a span of time:

    duration : seconds the phase lasts
    gas      : target MQ9 raw ADC setpoint (0-4095). Baseline ~700.
    temp     : target temperature setpoint in Celsius. Baseline ~25.
    flame    : is a real flame physically present? (drives FLAME pin LOW)
    motion   : is there physical motion? (drives PIR pin HIGH)
    door     : is the door physically open? (drives DOOR pin HIGH)
    label    : human-readable name of what is happening

The simulator ramps the analog values (gas/temp) smoothly toward the setpoint
and adds noise, so the output looks like a real sensor. Booleans are fed
through the SAME hysteresis state machines the real device uses.

The "demo" scenario LOOPS forever so the dashboard cycles through every state.
Other scenarios hold their final phase effectively forever.
"""

# Baseline "everything calm" physical state.
NORMAL = {
    "gas": 700,
    "temp": 25.0,
    "flame": False,
    "motion": False,
    "door": False,
    "label": "normal",
}

_FOREVER = 36000  # 10 hours ~= "until you stop it"


def phase(duration, **overrides):
    p = dict(NORMAL)
    p.update(overrides)
    p["duration"] = duration
    return p


# gas=2600 is above the 2000 default threshold -> gas_alarm.
# temp=56 is above the 50 default threshold      -> temp_alarm.
SCENARIOS = {
    # Calm forever — good for testing "no alarm" state.
    "normal": [phase(_FOREVER, label="normal")],

    # Calm, then a gas leak that stays.
    "gas_leak": [
        phase(8, label="normal"),
        phase(_FOREVER, gas=2600, label="gas_leak"),
    ],

    # Fire: flame present + temperature climbs.
    "fire": [
        phase(8, label="normal"),
        phase(_FOREVER, flame=True, temp=58, label="fire"),
    ],

    # Intrusion: motion + door opened.
    "intrusion": [
        phase(8, label="normal"),
        phase(_FOREVER, motion=True, door=True, label="intrusion"),
    ],

    # Demo: cycles through every alarm type, with calm "recover" gaps.
    # Loops forever. Flame phases are long because flame needs ~8s of
    # consecutive LOW reads (4 cycles x 2s) to confirm, matching the device.
    "demo": [
        phase(12, label="normal"),
        phase(16, gas=2600, label="gas_leak"),
        phase(10, label="recover"),
        phase(16, temp=56, label="high_temp"),
        phase(10, label="recover"),
        phase(22, flame=True, label="fire"),
        phase(10, label="recover"),
        phase(14, motion=True, door=True, label="intrusion"),
        phase(10, label="recover"),
    ],
}

# Scenarios that should loop back to the start when finished.
LOOPING = {"demo"}


def setpoints_at(scenario_name, elapsed):
    """Return the physical setpoint dict active at `elapsed` seconds."""
    phases = SCENARIOS.get(scenario_name, SCENARIOS["normal"])
    total = sum(p["duration"] for p in phases)

    if scenario_name in LOOPING and total > 0:
        elapsed = elapsed % total

    t = 0.0
    for p in phases:
        if elapsed < t + p["duration"]:
            return p
        t += p["duration"]
    return phases[-1]
