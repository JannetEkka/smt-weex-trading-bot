import os, json, time
SETTINGS_FILE = "smt_settings.json"
_cache = {}
_last_load = 0

def get_settings():
    global _cache, _last_load
    if time.time() - _last_load < 60 and _cache:
        return _cache
    _last_load = time.time()
    defaults = {"confidence_threshold": 0.85, "pause_trading": False, "emergency_exit_all": False, "enable_longs": True, "enable_shorts": False, "tp_multiplier": 1.0, "sl_multiplier": 1.0}
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as f:
                _cache = {**defaults, **json.load(f)}
        else:
            _cache = defaults
    except:
        _cache = defaults
    return _cache

def get_confidence_threshold(): return get_settings().get("confidence_threshold", 0.70)
def should_pause(): return get_settings().get("pause_trading", False)
def should_emergency_exit(): return get_settings().get("emergency_exit_all", False)
def is_direction_enabled(d): return get_settings().get(f"enable_{d.lower()}s", True)
def get_tp_sl_multipliers(): s = get_settings(); return s.get("tp_multiplier", 1.0), s.get("sl_multiplier", 1.0)
