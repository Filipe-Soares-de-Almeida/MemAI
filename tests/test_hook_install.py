"""`memai-hook install`: the merge into a host settings file, and the note
the server adds while no hook is registered for the session."""

from __future__ import annotations

import json

import pytest

from memai import hook, hook_install, server


@pytest.fixture
def settings(tmp_path):
    return tmp_path / "settings.json"


def _run(*argv) -> int:
    return hook.main(["install", *argv])


def _hooks(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))["hooks"]


def test_install_registers_the_three_events(settings, capsys):
    assert _run("--settings", str(settings)) == 0
    hooks = _hooks(settings)
    assert set(hooks) == set(hook_install.EVENTS.values())
    command = hooks["SessionStart"][0]["hooks"][0]["command"]
    assert command.endswith("session-start")
    assert "registered" in capsys.readouterr().out


def test_a_windows_path_is_written_with_forward_slashes(settings):
    """The registered command carries no backslash on any platform."""
    _run("--settings", str(settings))
    assert "\\" not in _hooks(settings)["Stop"][0]["hooks"][0]["command"]


def test_running_it_twice_registers_the_hooks_once(settings, capsys):
    _run("--settings", str(settings))
    first = settings.read_text(encoding="utf-8")
    assert _run("--settings", str(settings)) == 0
    assert "nothing to do" in capsys.readouterr().out
    assert settings.read_text(encoding="utf-8") == first
    assert len(_hooks(settings)["SessionStart"]) == 1


def test_somebody_elses_hook_on_the_same_event_survives(settings):
    settings.write_text(json.dumps({
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "echo hi"}]}]},
        "model": "opus",
    }), encoding="utf-8")
    _run("--settings", str(settings))
    groups = _hooks(settings)["SessionStart"]
    assert len(groups) == 2
    assert groups[0]["hooks"][0]["command"] == "echo hi"
    assert json.loads(settings.read_text(encoding="utf-8"))["model"] == "opus"


def test_a_stale_memai_entry_is_replaced_not_appended(settings):
    settings.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "C:/old/memai-hook.exe stop"}]}]}}),
        encoding="utf-8")
    _run("--settings", str(settings))
    assert len(_hooks(settings)["Stop"]) == 1


def test_the_file_it_overwrites_is_copied_aside_first(settings, capsys):
    settings.write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    _run("--settings", str(settings))
    backups = list(settings.parent.glob("settings.json.bak-*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"model": "opus"}
    assert "backed up" in capsys.readouterr().out


def test_an_unreadable_settings_file_is_not_a_reason_to_stop(settings):
    """Unparseable settings are replaced, with the copy kept beside them."""
    settings.write_text("{ this is not json", encoding="utf-8")
    assert _run("--settings", str(settings)) == 0
    assert set(_hooks(settings)) == set(hook_install.EVENTS.values())


def test_print_writes_nothing(settings, capsys):
    assert _run("--settings", str(settings), "--print") == 0
    assert not settings.exists()
    assert "SessionStart" in capsys.readouterr().out


def test_check_reports_what_is_missing(settings, capsys):
    assert _run("--settings", str(settings), "--check") == 1
    assert "not registered" in capsys.readouterr().out
    _run("--settings", str(settings))
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check") == 0


def test_an_install_flag_on_its_own_means_install(settings, capsys):
    assert hook.main(["--settings", str(settings), "--check"]) == 1
    assert "not registered" in capsys.readouterr().out


def test_no_event_and_no_install_flag_is_an_error():
    with pytest.raises(SystemExit):
        hook.main([])


def test_the_default_target_is_the_user_settings():
    assert hook_install.user_settings_path().parts[-2:] == (".claude", "settings.json")
    assert hook_install.project_settings_path().parts[-2:] == (".claude", "settings.local.json")


# ------------------------------------------- telling the server it is missing

def test_the_server_asks_for_the_hooks_when_none_is_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: tmp_path / "project.json")
    assert "no memai hook is registered" in server._instructions()


def test_the_note_spells_out_the_absolute_command(tmp_path, monkeypatch):
    """`memai-hook` is on PATH only for a shell with the environment
    activated, and a host's shell is not one."""
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: tmp_path / "project.json")
    monkeypatch.setattr(hook_install, "hook_command", lambda: "/opt/env/bin/memai-hook")
    assert "/opt/env/bin/memai-hook install" in server._instructions()


def test_a_command_with_a_space_is_quoted(tmp_path, monkeypatch):
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: tmp_path / "project.json")
    monkeypatch.setattr(hook_install, "hook_command", lambda: "C:/Program Files/env/memai-hook.exe")
    assert '"C:/Program Files/env/memai-hook.exe" install' in server._instructions()


def test_a_registration_in_the_project_answers_for_the_project(tmp_path, monkeypatch):
    project = tmp_path / "project.json"
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: project)
    _run("--settings", str(project))
    assert "no memai hook is registered" not in server._instructions()


def test_a_registration_elsewhere_does_not_answer_for_this_project(tmp_path, monkeypatch):
    """A registration for another repository does not count for this one."""
    elsewhere = tmp_path / "other-project.json"
    _run("--settings", str(elsewhere))
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: tmp_path / "user.json")
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: tmp_path / "project.json")
    assert "no memai hook is registered" in server._instructions()


def test_a_hand_edited_command_still_counts_as_registered(tmp_path, monkeypatch):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"hooks": {host: [{"hooks": [
        {"type": "command", "command": f"C:/elsewhere/memai-hook.exe {event}"}]}]
        for event, host in hook_install.EVENTS.items()}}), encoding="utf-8")
    monkeypatch.setattr(hook_install, "user_settings_path", lambda: user)
    monkeypatch.setattr(hook_install, "project_settings_path", lambda: tmp_path / "project.json")
    assert "no memai hook is registered" not in server._instructions()


def test_check_reports_a_registration_of_another_command(settings, capsys):
    settings.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "C:/old/memai-hook.exe stop"}]}]}}), encoding="utf-8")
    assert _run("--settings", str(settings), "--check") == 1
    assert "registers another command" in capsys.readouterr().out
