from aurora.inference.audit import InferenceAuditEvent

def test_audit_event_is_json_serializable():
    event=InferenceAuditEvent("m","nvidia_api",True,"2026-09-26T00:00:00+00:00",12)
    payload=event.to_json()
    assert '"success": true' in payload
    assert '"provider": "nvidia_api"' in payload
