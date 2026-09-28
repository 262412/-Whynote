import json

import pytest

from whynote.provider_keys import load_provider_key


def test_explicit_provider_does_not_fall_back_to_another_key(tmp_path):
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"deepseek": "synthetic-deepseek", "typesafe": ""}), encoding="utf-8")
    assert load_provider_key(path, "deepseek") == "synthetic-deepseek"
    with pytest.raises(ValueError, match="empty/invalid"):
        load_provider_key(path, "typesafe")


@pytest.mark.parametrize("content", ['{"deepseek": "SECRET"', '{"deepseek": "SECRET\\n"}', "[]"])
def test_key_errors_do_not_echo_file_content(tmp_path, content):
    path = tmp_path / "keys.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        load_provider_key(path, "deepseek")
    assert "SECRET" not in str(exc.value)


def test_missing_file_and_relative_path_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        load_provider_key(tmp_path / "missing", "deepseek")
    with pytest.raises(ValueError):
        load_provider_key("keys.json", "deepseek")
