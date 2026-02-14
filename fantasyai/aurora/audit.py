# fantasyai/aurora/audit.py
import json, time, uuid, os

AURORA_LOG = "aurora_experiments.jsonl"

def log_event(event_type, payload):
    rec = {
        "id": str(uuid.uuid4()),
        "ts": time.time(),
        "event_type": event_type,
        "payload": payload,
        "version": "aurora-v0.9.0"
    }
    with open(AURORA_LOG, "a") as f:
        f.write(json.dumps(rec) + "\n")
