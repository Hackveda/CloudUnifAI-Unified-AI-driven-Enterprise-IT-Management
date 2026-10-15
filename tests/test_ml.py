from app.ml.pipeline import allowed_playbooks, rank


def test_unknown_stays_read_only():
    result = rank([{"id":"1","source":"logs","service":"a","kind":"log","severity":"low","message":"unknown issue","occurred_at":"2026-10-15T01:00:00+00:00"}])
    assert result["unknown"]
    assert allowed_playbooks(result) == ["notify-oncall"]


def test_root_cause_evidence_and_order():
    events = [
        {"id":"a","source":"ci","service":"checkout","kind":"deployment","severity":"low","message":"deployment release started","occurred_at":"2026-10-15T01:00:00+00:00"},
        {"id":"b","source":"prometheus","service":"checkout","kind":"alert","severity":"critical","message":"errors after deployment","occurred_at":"2026-10-15T01:01:00+00:00"},
        {"id":"c","source":"itsm","service":"checkout","kind":"ticket","severity":"high","message":"rollout broke checkout","occurred_at":"2026-10-15T01:02:00+00:00"},
    ]
    result = rank(events)
    assert result["causes"][0]["cause"] == "deployment_regression"
    assert set(result["causes"][0]["evidence_ids"]) == {"a","b","c"}
    assert result["model_version"]
    assert result["calibrated"] is False
    assert "notify-oncall" in allowed_playbooks(result)
