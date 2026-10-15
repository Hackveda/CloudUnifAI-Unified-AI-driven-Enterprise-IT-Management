"""Deterministic, inspectable incident-feature and candidate ranking pipeline.

Optional trained linear weights are loaded from JSON only (no pickle/deserialization).
Scores are uncalibrated ranking scores, not causal probabilities.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from math import exp
from pathlib import Path
import json
import os
import re

MODEL_PATH = os.getenv("CLOUDUNIFAI_MODEL_PATH", "/data/rca-model.json")
KEYWORDS = {
    "deployment_regression": ("deploy", "rollout", "release", "rollback", "version", "after deployment"),
    "connection_pool_exhaustion": ("connection", "pool", "timeout", "database", "db", "exhaust"),
    "resource_saturation": ("cpu", "memory", "oom", "disk", "saturat", "capacity"),
    "network_failure": ("network", "dns", "packet", "gateway", "unreachable"),
    "cost_anomaly": ("billing", "spend", "cost", "finops", "expense"),
}
PLAYBOOKS = {
    "deployment_regression": "rollback-deployment",
    "connection_pool_exhaustion": "canary-scale-db-pool",
    "resource_saturation": "notify-oncall",
    "network_failure": "notify-oncall",
    "cost_anomaly": "notify-oncall",
}
MODEL_VERSION = "rules-v2"


def features(events):
    """Aggregate evidence by incident: no cross-service or tenant inference."""
    events = list(events)
    tokens = [str(e["message"]).lower() for e in events]
    kinds = Counter(e["kind"] for e in events)
    text = " ".join(tokens)
    timestamps = []
    for e in events:
        try:
            timestamps.append(datetime.fromisoformat(e["occurred_at"]).timestamp())
        except (KeyError, TypeError, ValueError):
            pass
    spread = max(timestamps) - min(timestamps) if len(timestamps) >= 2 else 0.0
    result = {
        "event_count": len(events),
        "source_count": len({e["source"] for e in events}),
        "kind_count": len(kinds),
        "window_seconds": max(0.0, spread),
        "cross_source": int(len({e["source"] for e in events}) > 1),
        "has_deployment": int(kinds["deployment"] > 0 or kinds["change"] > 0),
        "has_alert": int(kinds["alert"] > 0),
        "has_billing": int(kinds["billing"] > 0),
        "has_ticket": int(kinds["ticket"] > 0),
        "critical": int(any(e["severity"] == "critical" for e in events)),
    }
    for cause, terms in KEYWORDS.items():
        result["term_" + cause] = sum(int(any(term in t for term in terms)) for t in tokens)
    return result


def _weights():
    try:
        model = json.loads(Path(MODEL_PATH).read_text())
        if model.get("schema") != 1 or not isinstance(model.get("weights"), dict):
            raise ValueError("Bad model schema")
        expected = set(KEYWORDS)
        if set(model["weights"]) != expected:
            raise ValueError("Unexpected classes")
        for cls, weights in model["weights"].items():
            if not isinstance(weights, dict) or any(not isinstance(v, (int, float)) or abs(v) > 100 for v in weights.values()):
                raise ValueError("Invalid model weights")
        return model["weights"], str(model.get("version", "linear-v1"))
    except (OSError, ValueError, TypeError):
        return None, MODEL_VERSION


def rank(events):
    evs = list(events)
    if not evs:
        return {"model_version": MODEL_VERSION, "features": {}, "causes": [], "unknown": True}
    feat = features(evs)
    learned, version = _weights()
    scored = []
    for cause in KEYWORDS:
        # Cross-signal evidence and temporally nearby observations strengthen rankings,
        # but a lone event should never generate high-confidence remediation advice.
        if learned:
            weights = learned[cause]
            z = float(weights.get("_bias", 0))
            z += sum(float(weights.get(k, 0)) * float(v) for k, v in feat.items())
        else:
            z = (-2.7 + 0.75 * min(3, feat["term_" + cause])
                 + 0.33 * feat["cross_source"] + 0.25 * feat["has_alert"]
                 + (0.55 * feat["has_deployment"] if cause == "deployment_regression" else 0)
                 + (0.5 * feat["has_billing"] if cause == "cost_anomaly" else 0))
        score = 1 / (1 + exp(-max(-30, min(30, z))))
        matching = [e["id"] for e in evs if any(t in e["message"].lower() for t in KEYWORDS[cause])]
        if cause == "deployment_regression":
            matching += [e["id"] for e in evs if e["kind"] in ("deployment", "change")]
        scored.append({"cause": cause, "score": round(score, 4),
                       "evidence_ids": sorted(set(matching)), "proposed_playbook": PLAYBOOKS[cause]})
    scored.sort(key=lambda c: c["score"], reverse=True)
    # Reject when evidence is thin; scores are NOT calibrated probabilities.
    unknown = scored[0]["score"] < 0.45 or (not scored[0]["evidence_ids"])
    return {"model_version": version, "features": feat, "causes": scored[:3],
            "unknown": unknown, "calibrated": False, "mode": "recommendation_only"}


def allowed_playbooks(prediction):
    """ML can suggest, but policy independently limits executable candidates."""
    if prediction["unknown"]:
        return ["notify-oncall"]
    top = prediction["causes"][0]
    if top["score"] < 0.7 or len(top["evidence_ids"]) < 2:
        return ["notify-oncall"]
    return sorted({"notify-oncall", top["proposed_playbook"]})
