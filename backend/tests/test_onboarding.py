"""首启引导测试。

只用临时目录，不触碰真实数据目录，不联网。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app import onboarding


class TestCheckDataDir:
    """目录状态判定。"""

    def test_missing_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "absent"
        status = onboarding.check_data_dir(target)
        assert status.status == onboarding.STATUS_MISSING
        assert status.exists is False
        assert status.first_run is True

    def test_empty_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "empty"
        target.mkdir()
        status = onboarding.check_data_dir(target)
        assert status.status == onboarding.STATUS_EMPTY
        assert status.writable is True
        assert status.file_count == 0
        assert status.first_run is True

    def test_path_points_to_file(self, tmp_path: Path) -> None:
        target = tmp_path / "file"
        target.write_text("x", encoding="utf-8")
        status = onboarding.check_data_dir(target)
        assert status.status == onboarding.STATUS_NOT_WRITABLE
        assert status.writable is False

    def test_directory_under_file_is_not_writable(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        status = onboarding.check_data_dir(blocker / "child")
        assert status.status == onboarding.STATUS_NOT_WRITABLE

    def test_readonly_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "readonly"
        target.mkdir()
        os.chmod(target, 0o500)
        try:
            status = onboarding.check_data_dir(target)
            if status.writable:
                pytest.skip("当前平台未生效目录只读属性")
            assert status.status == onboarding.STATUS_NOT_WRITABLE
        finally:
            os.chmod(target, 0o700)

    def test_populated_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "used"
        target.mkdir()
        (target / "settings.json").write_text("{}", encoding="utf-8")
        status = onboarding.check_data_dir(target)
        assert status.status == onboarding.STATUS_OK
        assert status.file_count == 1
        assert status.first_run is False


class TestDefaults:
    """默认设置补齐。"""

    def test_default_settings_contains_required_keys(self) -> None:
        defaults = onboarding.default_settings()
        for key in onboarding.REQUIRED_SETTING_KEYS:
            assert key in defaults

    def test_apply_defaults_keeps_existing_values(self) -> None:
        merged = onboarding.apply_defaults({"theme": "light", "inference_url": "http://x"})
        assert merged["theme"] == "light"
        assert merged["inference_url"] == "http://x"
        assert merged["memory_mode"] == "hybrid"

    def test_apply_defaults_does_not_mutate_input(self) -> None:
        source = {"theme": "light"}
        onboarding.apply_defaults(source)
        assert source == {"theme": "light"}

    def test_apply_defaults_with_none(self) -> None:
        merged = onboarding.apply_defaults(None)
        assert merged["theme"] == "dark"

    def test_missing_keys(self) -> None:
        assert onboarding.missing_keys({"theme": "light"}) == [
            key for key in onboarding.REQUIRED_SETTING_KEYS if key != "theme"
        ]
        assert onboarding.missing_keys(onboarding.default_settings()) == []


class TestPlan:
    """引导步骤随状态变化。"""

    def test_missing_directory_step(self, tmp_path: Path) -> None:
        status = onboarding.check_data_dir(tmp_path / "absent")
        steps = onboarding.plan(status, onboarding.default_settings())
        ids = [step["id"] for step in steps]
        assert "create_data_dir" in ids
        assert "fix_permission" not in ids

    def test_not_writable_step(self, tmp_path: Path) -> None:
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        status = onboarding.check_data_dir(blocker)
        steps = onboarding.plan(status, onboarding.default_settings())
        ids = [step["id"] for step in steps]
        assert "fix_permission" in ids
        assert "create_data_dir" not in ids

    def test_empty_directory_step(self, tmp_path: Path) -> None:
        target = tmp_path / "empty"
        target.mkdir()
        status = onboarding.check_data_dir(target)
        steps = onboarding.plan(status, onboarding.default_settings())
        ids = [step["id"] for step in steps]
        assert "init_data" in ids

    def test_missing_settings_add_steps(self, tmp_path: Path) -> None:
        target = tmp_path / "used"
        target.mkdir()
        (target / "a.txt").write_text("a", encoding="utf-8")
        status = onboarding.check_data_dir(target)
        steps = onboarding.plan(status, {})
        ids = [step["id"] for step in steps]
        assert "fill_settings" in ids
        assert "set_inference_url" in ids

    def test_complete_state_only_keeps_model_check(self, tmp_path: Path) -> None:
        target = tmp_path / "used"
        target.mkdir()
        (target / "a.txt").write_text("a", encoding="utf-8")
        status = onboarding.check_data_dir(target)
        steps = onboarding.plan(status, onboarding.default_settings())
        ids = [step["id"] for step in steps]
        assert ids == ["check_models"]


class TestBootstrap:
    """首启准备。"""

    def test_bootstrap_creates_directory_and_defaults(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "xw"
        os.environ["XWORLD_DATA_DIR"] = str(data_dir)
        try:
            report = onboarding.bootstrap()
            assert report["status"] in (onboarding.STATUS_EMPTY, onboarding.STATUS_OK)
            assert data_dir.is_dir()
            settings_path = data_dir / "settings.json"
            assert settings_path.is_file()
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
            for key in onboarding.REQUIRED_SETTING_KEYS:
                assert key in payload
        finally:
            os.environ.pop("XWORLD_DATA_DIR", None)

    def test_bootstrap_keeps_existing_values(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "xw"
        data_dir.mkdir()
        (data_dir / "settings.json").write_text(
            json.dumps({"theme": "light"}), encoding="utf-8"
        )
        os.environ["XWORLD_DATA_DIR"] = str(data_dir)
        try:
            onboarding.bootstrap()
            payload = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
            assert payload["theme"] == "light"
            assert payload["memory_mode"] == "hybrid"
        finally:
            os.environ.pop("XWORLD_DATA_DIR", None)
