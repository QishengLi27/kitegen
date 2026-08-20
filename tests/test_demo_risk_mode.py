"""Tests for the risk-mode feature (conservative / normal / aggressive).

Covers the config layer (demo.paper), the numeric tool profiles
(demo.tools), and the mode-specific agent prompts (demo.agents).
"""

import pytest


# ── demo.paper: PaperConfig + update helper ──────────────────────────────────


def test_paper_config_accepts_valid_risk_modes():
    from demo.paper import PaperConfig

    for mode in ("conservative", "normal", "aggressive"):
        config = PaperConfig(risk_mode=mode)
        assert config.risk_mode == mode


def test_paper_config_normalizes_risk_mode_case():
    from demo.paper import PaperConfig

    assert PaperConfig(risk_mode="AGGRESSIVE").risk_mode == "aggressive"
    assert PaperConfig(risk_mode=" Normal ").risk_mode == "normal"


def test_paper_config_rejects_unknown_risk_mode():
    from demo.paper import PaperConfig

    with pytest.raises(ValueError, match="risk_mode"):
        PaperConfig(risk_mode="yolo")


def test_paper_defaults_for_mode():
    from demo.paper import paper_defaults_for_mode

    cons = paper_defaults_for_mode("conservative")
    norm = paper_defaults_for_mode("normal")
    aggr = paper_defaults_for_mode("aggressive")

    # Aggressive takes bigger positions and tolerates deeper drawdowns
    assert aggr["max_position_pct"] > norm["max_position_pct"] > cons["max_position_pct"]
    assert aggr["stop_loss_pct"] > norm["stop_loss_pct"] > cons["stop_loss_pct"]
    # Unknown modes fall back to normal
    assert paper_defaults_for_mode("yolo") == norm


def test_update_config_mode_change_adopts_profile_defaults():
    from demo.paper import PaperConfig, update_config_from_dict

    current = PaperConfig()  # normal
    updated = update_config_from_dict(current, {"risk_mode": "aggressive"})

    assert updated.risk_mode == "aggressive"
    # Matches the user's chosen PAPER_RISK_PROFILES for aggressive
    assert updated.max_position_pct == 0.80
    assert updated.stop_loss_pct == 0.50
    # Other fields untouched
    assert updated.initial_capital == current.initial_capital
    assert updated.t_plus_1 is True


def test_update_config_explicit_values_beat_mode_defaults():
    from demo.paper import PaperConfig, update_config_from_dict

    current = PaperConfig()
    updated = update_config_from_dict(current, {
        "risk_mode": "aggressive",
        "max_position_pct": 0.15,
    })

    # Explicit value wins; the other derived field follows the profile
    assert updated.max_position_pct == 0.15
    assert updated.stop_loss_pct == 0.50


def test_update_config_same_mode_keeps_custom_params():
    from demo.paper import PaperConfig, update_config_from_dict

    current = PaperConfig(risk_mode="aggressive", max_position_pct=0.5, stop_loss_pct=0.25)
    updated = update_config_from_dict(current, {"risk_mode": "aggressive"})

    # No mode change → keep the user's custom values, don't reset to profile
    assert updated.max_position_pct == 0.5
    assert updated.stop_loss_pct == 0.25


def test_update_config_rejects_unknown_mode():
    from demo.paper import PaperConfig, update_config_from_dict

    with pytest.raises(ValueError, match="risk_mode"):
        update_config_from_dict(PaperConfig(), {"risk_mode": "yolo"})


def test_config_roundtrip_persists_risk_mode(tmp_path, monkeypatch):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)

    from demo.paper import PaperConfig, load_config, save_config

    save_config(PaperConfig(risk_mode="conservative", check_interval_min=7))
    loaded = load_config()
    assert loaded.risk_mode == "conservative"
    assert loaded.check_interval_min == 7


def test_load_config_risk_mode_env_fallback(tmp_path, monkeypatch):
    """A fresh config file (or none) falls back to the RISK_MODE env var."""
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)
    monkeypatch.setenv("RISK_MODE", "aggressive")

    from demo.paper import load_config

    assert load_config().risk_mode == "aggressive"


def test_load_config_risk_mode_file_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setattr("demo.paper.DATA_DIR", tmp_path)
    monkeypatch.setenv("RISK_MODE", "aggressive")

    from demo.paper import PaperConfig, load_config, save_config

    save_config(PaperConfig(risk_mode="conservative"))
    assert load_config().risk_mode == "conservative"


# ── demo.tools: risk profiles + context ─────────────────────────────────────


def test_risk_profiles_ordering():
    from demo.tools import RISK_PROFILES

    # Aggressive risks more per trade and triggers signals more easily
    assert (
        RISK_PROFILES["aggressive"]["risk_pct"]
        > RISK_PROFILES["normal"]["risk_pct"]
        > RISK_PROFILES["conservative"]["risk_pct"]
    )
    assert (
        RISK_PROFILES["aggressive"]["rsi_bull"]
        <= RISK_PROFILES["normal"]["rsi_bull"]
        <= RISK_PROFILES["conservative"]["rsi_bull"]
    )
    # Aggressive uses wider stops and farther targets
    assert (
        RISK_PROFILES["aggressive"]["atr_stop_mult"]
        >= RISK_PROFILES["normal"]["atr_stop_mult"]
    )
    assert (
        RISK_PROFILES["aggressive"]["atr_tp_mult"]
        > RISK_PROFILES["normal"]["atr_tp_mult"]
        > RISK_PROFILES["conservative"]["atr_tp_mult"]
    )


def test_set_get_risk_mode_roundtrip():
    from demo.tools import get_risk_mode, set_risk_mode

    original = get_risk_mode()
    try:
        set_risk_mode("aggressive")
        assert get_risk_mode() == "aggressive"
        set_risk_mode("Conservative")
        assert get_risk_mode() == "conservative"
    finally:
        set_risk_mode(original)


def test_set_risk_mode_rejects_unknown():
    from demo.tools import set_risk_mode

    with pytest.raises(ValueError, match="risk_mode"):
        set_risk_mode("yolo")


def test_risk_profile_helpers():
    from demo.tools import risk_profile

    assert risk_profile("aggressive")["risk_pct"] == 2.0
    assert risk_profile("normal")["risk_pct"] == 1.0
    assert risk_profile("conservative")["risk_pct"] == 0.5
    # Unknown falls back to normal
    assert risk_profile("yolo")["risk_pct"] == 1.0


# ── demo.agents: mode-specific prompts ───────────────────────────────────────


def test_risk_mode_instructions_exist_for_all_modes():
    from demo.agents import RISK_MODE_INSTRUCTIONS

    assert set(RISK_MODE_INSTRUCTIONS) == {"conservative", "normal", "aggressive"}


def test_make_agents_embeds_mode_in_goals():
    from demo.agents import make_agents

    for mode, keyword in (
        ("conservative", "CONSERVATIVE"),
        ("normal", "NORMAL"),
        ("aggressive", "AGGRESSIVE"),
    ):
        analyst, researcher, strategist = make_agents(mode)
        assert keyword in analyst.goal, f"{mode} missing from analyst goal"
        assert keyword in researcher.goal, f"{mode} missing from researcher goal"
        assert keyword in strategist.goal, f"{mode} missing from strategist goal"


def test_make_agents_personalities_differ_by_mode():
    from demo.agents import make_agents

    personalities = {
        mode: make_agents(mode)[0].personality
        for mode in ("conservative", "normal", "aggressive")
    }
    assert len(set(personalities.values())) == 3


def test_build_pipeline_for_each_mode():
    from demo.agents import build_pipeline

    for mode in ("conservative", "normal", "aggressive"):
        compiled, saver = build_pipeline(mode)
        assert compiled is not None
        assert saver is not None


def test_pipelines_exist_for_all_modes():
    from demo.agents import PIPELINES, get_pipeline

    assert set(PIPELINES) == {"conservative", "normal", "aggressive"}
    # get_pipeline returns the mode's cached pipeline
    assert get_pipeline("aggressive") is PIPELINES["aggressive"]
    assert get_pipeline("Conservative") is PIPELINES["conservative"]
    # Unknown modes fall back to normal
    assert get_pipeline("yolo") is PIPELINES["normal"]
    assert get_pipeline("") is PIPELINES["normal"]
