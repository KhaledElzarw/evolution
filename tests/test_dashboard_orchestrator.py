import dashboard_orchestrator


def test_orchestrator_python_executable_defaults_to_venv(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert dashboard_orchestrator.get_python_executable() == dashboard_orchestrator.PYTHON


def test_orchestrator_python_executable_honors_override(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert dashboard_orchestrator.get_python_executable() == "/custom/python"


def test_orchestrator_run_uses_configurable_python(monkeypatch):
    calls = []

    class Result:
        stdout = "123\n"

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return Result()

    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")
    monkeypatch.setattr(dashboard_orchestrator.subprocess, "run", fake_run)

    assert dashboard_orchestrator._run("engine", "status") == "123"
    assert calls[0][0][0] == "/custom/python"


def test_orchestrator_status_queries_all_runtime_services(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-pid"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.status() == {
        "engine": "engine-pid",
        "dashboard": "dashboard-pid",
        "ai": "ai-pid",
    }
    assert calls == [("engine", "status"), ("dashboard", "status"), ("ai", "status")]


def test_orchestrator_restart_stops_before_starting(monkeypatch):
    calls = []

    def fake_run(service, command):
        calls.append((service, command))
        return f"{service}-{command}"

    monkeypatch.setattr(dashboard_orchestrator, "_run", fake_run)

    assert dashboard_orchestrator.restart() == {
        "engine": "engine-start",
        "dashboard": "dashboard-start",
        "ai": "ai-start",
    }
    assert calls == [
        ("ai", "stop"),
        ("engine", "stop"),
        ("dashboard", "stop"),
        ("engine", "start"),
        ("dashboard", "start"),
        ("ai", "start"),
    ]
