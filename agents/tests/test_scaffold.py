from pathlib import Path
from typing import Any
import pytest

import agents.scaffold as scaffold
from agents.feature import FeatureTarget


class TestScaffold:
    def test_format_spec_overview(self) -> None:
        assert scaffold.format_spec_overview("my overview") == "my overview"
        assert scaffold.format_spec_overview("") == scaffold.DEFAULT_OVERVIEW

    def test_scaffold_new_feature_template_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        slice_dir = tmp_path / "features" / "billing" / "Checkout"
        target = FeatureTarget(
            name="Checkout",
            domain="billing",
            dir=slice_dir,
            root=tmp_path / "features",
            config_path=tmp_path / ".features.json",
        )
        monkeypatch.setattr(scaffold, "generate_spec_with_ai", lambda *a, **k: None)

        scaffold.scaffold_new_feature(target, "overview prompt")

        assert (slice_dir / "spec.md").exists()
        assert (slice_dir / "Schema.py").exists()
        assert (slice_dir / "Handler.py").exists()
        assert (slice_dir / "Controller.py").exists()
        assert (slice_dir / "Tests.py").exists()
        assert (slice_dir / "__init__.py").exists()
        assert (tmp_path / ".features.json").exists()

    def test_scaffold_new_feature_no_controller(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        slice_dir = tmp_path / "features" / "billing" / "Worker"
        target = FeatureTarget(
            name="Worker",
            domain="billing",
            dir=slice_dir,
            root=tmp_path / "features",
            config_path=tmp_path / ".features.json",
        )
        monkeypatch.setattr(scaffold, "generate_spec_with_ai", lambda *a, **k: None)

        scaffold.scaffold_new_feature(target, "", no_controller=True)

        assert (slice_dir / "Schema.py").exists()
        assert (slice_dir / "Handler.py").exists()
        assert (slice_dir / "Tests.py").exists()
        assert not (slice_dir / "Controller.py").exists()

    def test_scaffold_new_feature_custom_canonical(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        slice_dir = tmp_path / "features" / "pipeline" / "Sync"
        target = FeatureTarget(
            name="Sync",
            domain="pipeline",
            dir=slice_dir,
            root=tmp_path / "features",
            config_path=tmp_path / ".features.json",
            canonical_files=frozenset({"Schema.py", "Worker.py", "Tests.py"}),
        )
        monkeypatch.setattr(scaffold, "generate_spec_with_ai", lambda *a, **k: None)

        scaffold.scaffold_new_feature(target, "")

        assert (slice_dir / "Schema.py").exists()
        assert (slice_dir / "Worker.py").exists()
        assert (slice_dir / "Tests.py").exists()
        assert not (slice_dir / "Handler.py").exists()
        assert not (slice_dir / "Controller.py").exists()
