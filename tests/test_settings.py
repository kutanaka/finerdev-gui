from devgui.settings import Settings


def test_defaults_match_design_doc_section_12():
    s = Settings()
    assert s.host == "0.0.0.0"
    assert s.port == 8000
    assert s.title == "devgui"
    assert s.operator_idle_timeout == 600.0
    assert s.operator_disconnect_grace == 30.0
    assert s.takeover_wait == 10.0
    assert s.takeover_cooldown == 30.0
    assert s.slow_call_warning == 30.0


def test_fields_are_overridable():
    s = Settings(host="127.0.0.1", port=9000, title="lab")
    assert s.host == "127.0.0.1"
    assert s.port == 9000
    assert s.title == "lab"
