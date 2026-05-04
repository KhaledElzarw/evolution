import os
import subprocess

import wrapper_runner


def test_get_python_executable_returns_default_when_env_unset(monkeypatch):
    monkeypatch.delenv("TRADEBOT_PYTHON", raising=False)

    assert wrapper_runner.get_python_executable("/default/python") == "/default/python"


def test_get_python_executable_uses_tradebot_python_when_set(monkeypatch):
    monkeypatch.setenv("TRADEBOT_PYTHON", "/custom/python")

    assert wrapper_runner.get_python_executable("/default/python") == "/custom/python"


def test_start_detached_calls_popen_contract_and_returns_pid(monkeypatch, tmp_path):
    calls = []

    class Proc:
        pid = 4321

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return Proc()

    base_dir = tmp_path / "base"
    base_dir.mkdir()
    log_path = tmp_path / "wrapper.log"
    monkeypatch.setenv("TRADEBOT_WRAPPER_TEST", "present")
    monkeypatch.setattr(wrapper_runner.subprocess, "Popen", fake_popen)

    pid = wrapper_runner.start_detached(
        "/custom/python",
        "target.py",
        base_dir,
        log_path,
    )

    assert pid == 4321
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == ["/custom/python", "target.py"]
    assert set(kwargs) == {
        "cwd",
        "stdin",
        "stdout",
        "stderr",
        "start_new_session",
        "close_fds",
        "env",
    }
    assert kwargs["cwd"] == str(base_dir)
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"].name == str(log_path)
    assert kwargs["stdout"].closed
    assert kwargs["stderr"] == subprocess.STDOUT
    assert kwargs["start_new_session"] is True
    assert kwargs["close_fds"] is True
    assert isinstance(kwargs["env"], dict)
    assert kwargs["env"] is not os.environ
    assert kwargs["env"]["TRADEBOT_WRAPPER_TEST"] == "present"
    assert log_path.exists()


def test_write_pid_writes_string_pid_to_path(tmp_path):
    pid_path = tmp_path / "wrapper.pid"

    wrapper_runner.write_pid(pid_path, 12345)

    assert pid_path.read_text(encoding="utf-8") == "12345"


def test_unlink_pid_removes_existing_pid_path(tmp_path):
    pid_path = tmp_path / "wrapper.pid"
    pid_path.write_text("12345", encoding="utf-8")

    wrapper_runner.unlink_pid(pid_path)

    assert not pid_path.exists()


def test_unlink_pid_is_safe_when_file_is_missing(tmp_path):
    pid_path = tmp_path / "missing.pid"

    wrapper_runner.unlink_pid(pid_path)

    assert not pid_path.exists()
