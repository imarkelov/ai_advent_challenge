import json

from config import DEFAULT_CONFIG, load_config, mask_secret, resolve_provider, save_config


def test_default_config_has_five_roles(tmp_path):
    cfg = load_config(str(tmp_path))
    assert set(cfg["roles"]) == {"qa-lead", "researcher", "testwriter", "debugger", "reviewer"}
    assert cfg["critic"]["must_differ_from_author"] is True
    assert cfg["providers"]["ollama"]["base_url"] == "http://localhost:11435"
    assert cfg["android"]["avd"] == "Pixel_8_Pro_API_33"


def test_save_then_load_roundtrip(tmp_path):
    cfg = load_config(str(tmp_path))
    cfg["roles"]["reviewer"]["model"] = "gemma4:12b"
    save_config(str(tmp_path), cfg)
    assert load_config(str(tmp_path))["roles"]["reviewer"]["model"] == "gemma4:12b"


def test_partial_file_merges_over_defaults(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"roles": {"reviewer": {"model": "x"}}}), encoding="utf-8"
    )
    cfg = load_config(str(tmp_path))
    assert cfg["roles"]["reviewer"]["model"] == "x"
    assert cfg["roles"]["testwriter"]["model"] == DEFAULT_CONFIG["roles"]["testwriter"]["model"]


def test_mask_secret():
    assert mask_secret(None) == ""
    assert mask_secret("abc") == "***"
    assert mask_secret("gpusta_1234567890") == "gpus***7890"


def test_resolve_provider_for_role(tmp_path):
    cfg = load_config(str(tmp_path))
    provider = resolve_provider(cfg, "reviewer")
    assert provider["name"] == "ollama"
    assert provider["model"] == "gemma4:12b"
    assert provider["temperature"] == 0.1
