"""`memai-hook install`: the merge into a host settings file, the copy of the
bundled skills, and the note the server adds while no hook is registered for
the session."""

from __future__ import annotations

import json

import pytest

from memai import hook, hook_install, server


@pytest.fixture
def settings(tmp_path):
    return tmp_path / "settings.json"


@pytest.fixture
def bundled(tmp_path, monkeypatch):
    """Two synthetic skills standing in for whatever the package ships.

    The real `memai/skills/` is not read: what the tests below guarantee is
    the copy and its safety, not the contents of any shipped skill.
    """
    source = tmp_path / "bundled"
    for name, body in (("acme-cache-warmup", "warm the cache before the first request"),
                       ("zeta-queue-drain", "drain the queue in batches")):
        skill = source / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n\n{body}\n", encoding="utf-8")
    nested = source / "acme-cache-warmup" / "reference"
    nested.mkdir()
    (nested / "steps.md").write_text("1. warm\n2. verify\n", encoding="utf-8")
    monkeypatch.setattr(hook_install, "skills_source", lambda: source)
    return source


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
    _run("--settings", str(settings), "--skills")
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


# ------------------------------------------------------------------ the skills

def test_the_skills_go_beside_the_settings_file():
    """Either settings file lives in `.claude`, so the skills land in
    `.claude/skills`."""
    for path in (hook_install.user_settings_path(), hook_install.project_settings_path()):
        assert hook_install.skills_dir(path).parts[-2:] == (".claude", "skills")


def test_install_skills_copies_every_bundled_directory(bundled, settings, tmp_path, capsys):
    assert _run("--settings", str(settings), "--skills") == 0
    target = tmp_path / "skills"
    assert (target / "acme-cache-warmup" / "SKILL.md").exists()
    assert (target / "zeta-queue-drain" / "SKILL.md").exists()
    assert "installed" in capsys.readouterr().out


def test_a_nested_file_inside_a_skill_comes_along(bundled, settings, tmp_path):
    _run("--settings", str(settings), "--skills")
    nested = tmp_path / "skills" / "acme-cache-warmup" / "reference" / "steps.md"
    assert nested.read_text(encoding="utf-8") == "1. warm\n2. verify\n"


def test_the_hooks_are_not_registered_by_a_skills_install(bundled, settings):
    """--skills does one job: the settings file is left as it was."""
    assert _run("--settings", str(settings), "--skills") == 0
    assert not settings.exists()


def test_running_it_twice_copies_nothing_the_second_time(bundled, settings, tmp_path, capsys):
    _run("--settings", str(settings), "--skills")
    capsys.readouterr()
    assert _run("--settings", str(settings), "--skills") == 0
    assert "nothing to do" in capsys.readouterr().out
    assert not list((tmp_path / "skills").rglob("*.bak-*"))


def test_a_skill_file_it_overwrites_is_copied_aside_first(bundled, settings, tmp_path, capsys):
    target = tmp_path / "skills" / "acme-cache-warmup"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("hand-edited\n", encoding="utf-8")
    assert _run("--settings", str(settings), "--skills") == 0
    backups = list(target.glob("SKILL.md.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "hand-edited\n"
    assert "acme-cache-warmup" in (target / "SKILL.md").read_text(encoding="utf-8")
    assert "backed up" in capsys.readouterr().out


def test_a_skill_memai_does_not_ship_is_left_alone(bundled, settings, tmp_path):
    foreign = tmp_path / "skills" / "omni-report-export"
    foreign.mkdir(parents=True)
    (foreign / "SKILL.md").write_text("somebody else wrote this\n", encoding="utf-8")
    _run("--settings", str(settings), "--skills")
    assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "somebody else wrote this\n"
    assert not list(foreign.glob("*.bak-*"))


def test_print_copies_nothing(bundled, settings, tmp_path, capsys):
    assert _run("--settings", str(settings), "--skills", "--print") == 0
    assert not (tmp_path / "skills").exists()
    assert "acme-cache-warmup" in capsys.readouterr().out


def test_an_empty_skills_directory_installs_nothing_and_is_not_an_error(
        settings, tmp_path, monkeypatch, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(hook_install, "skills_source", lambda: empty)
    assert _run("--settings", str(settings), "--skills") == 0
    assert "nothing installed" in capsys.readouterr().out
    assert not (tmp_path / "skills").exists()


def test_a_missing_skills_directory_installs_nothing_and_is_not_an_error(
        settings, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hook_install, "skills_source", lambda: tmp_path / "not-there")
    assert _run("--settings", str(settings), "--skills") == 0
    assert "nothing installed" in capsys.readouterr().out


def test_a_dunder_directory_is_not_a_skill(bundled):
    (bundled / "__pycache__").mkdir()
    assert [p.name for p in hook_install.bundled_skills()] == [
        "acme-cache-warmup", "zeta-queue-drain"]


def test_check_reports_the_skills_next_to_the_hooks(bundled, settings, tmp_path, capsys):
    assert _run("--settings", str(settings), "--check") == 1
    out = capsys.readouterr().out
    assert "not registered" in out
    assert "acme-cache-warmup: missing" in out

    _run("--settings", str(settings))
    _run("--settings", str(settings), "--skills")
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check") == 0
    assert "acme-cache-warmup: installed" in capsys.readouterr().out


def test_check_skills_fails_on_a_skill_that_no_longer_matches_what_is_bundled(
        bundled, settings, tmp_path, capsys):
    _run("--settings", str(settings))
    _run("--settings", str(settings), "--skills")
    (tmp_path / "skills" / "zeta-queue-drain" / "SKILL.md").write_text(
        "edited since\n", encoding="utf-8")
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check", "--skills") == 1
    assert "zeta-queue-drain: outdated" in capsys.readouterr().out


def test_check_gates_on_the_hooks_and_check_skills_on_the_skills(
        bundled, settings, capsys):
    """An uninstalled skill is a choice; a missing hook is the store out of reach.

    Both are always reported -- only which one decides the exit code moves.
    """
    _run("--settings", str(settings))          # hooks only, no skills
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check") == 0
    assert "acme-cache-warmup: missing" in capsys.readouterr().out
    assert _run("--settings", str(settings), "--check", "--skills") == 1

    _run("--settings", str(settings), "--skills")
    settings.write_text("{}", encoding="utf-8")  # skills installed, hooks gone
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check", "--skills") == 0
    assert "acme-cache-warmup: installed" in capsys.readouterr().out
    assert _run("--settings", str(settings), "--check") == 1


def test_check_says_so_when_no_skill_is_bundled(settings, tmp_path, monkeypatch, capsys):
    """Bundling none is not a missing skill: only the hooks decide the exit code."""
    monkeypatch.setattr(hook_install, "skills_source", lambda: tmp_path / "not-there")
    _run("--settings", str(settings))
    capsys.readouterr()
    assert _run("--settings", str(settings), "--check") == 0
    assert "no skills bundled" in capsys.readouterr().out


def test_a_skills_flag_on_its_own_means_install(bundled, settings, tmp_path):
    assert hook.main(["--settings", str(settings), "--skills"]) == 0
    assert (tmp_path / "skills" / "zeta-queue-drain" / "SKILL.md").exists()


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
