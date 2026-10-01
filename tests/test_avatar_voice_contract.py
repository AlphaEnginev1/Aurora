from pathlib import Path


ROOT = Path(__file__).parents[1]
UI = ROOT / "aurora" / "dashboard" / "ui"


def _read(name: str) -> str:
    return (UI / name).read_text(encoding="utf-8")


def test_avatar_voice_adapter_exposes_provider_neutral_states():
    content = _read("avatar_voice.js")
    for state in (
        "IDLE",
        "LISTENING",
        "THINKING",
        "SPEAKING",
        "ERROR",
        "DISCONNECTED",
    ):
        assert f"{state}: \"{state}\"" in content
    assert "window.AuroraAvatarVoiceAdapter" in content


def test_avatar_voice_adapter_has_failure_isolation_and_fallback_paths():
    content = _read("avatar_voice.js")
    assert "speech_input_unavailable" in content
    assert "speech_output_unavailable" in content
    assert "microphone_denied" in content
    assert "speech_input_failed" in content
    assert "speech_output_failed" in content
    assert "dataset.avatarState" in content


def test_dashboard_loads_voice_adapter_without_provider_credentials():
    index = _read("index.html")
    content = _read("avatar_voice.js")
    assert "./avatar_voice.js" in index
    assert "api_key" not in content.lower()
    assert "gemini" not in content.lower()


def test_voice_controls_require_explicit_user_action():
    index = _read("index.html")
    assert 'id="avatar-listen" type="button"' in index
    assert 'id="avatar-stop" type="button"' in index


def test_avatar_voice_adapter_documents_text_fallback():
    content = _read("avatar_voice.js")
    assert "text_available" not in content
    assert 'this.setState(STATES.ERROR)' in content

def test_dashboard_wires_explicit_voice_controls_to_adapter():
    app = _read("app.js")
    assert "setupAvatarVoice" in app
    assert "startListening" in app
    assert "stopSpeaking" in app
    assert "Block 12E" in app


def test_voice_adapter_uses_secure_permission_capable_microphone_api():
    content = _read("avatar_voice.js")
    assert "getUserMedia({ audio: true })" in content
    assert "this._releaseStream()" in content
