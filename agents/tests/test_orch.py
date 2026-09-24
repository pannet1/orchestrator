import json
import sys
from pathlib import Path
import pytest

import agents.orch as orch


class TestOrch:
    def test_parse_args_with_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["orch.py", "new", "billing/Checkout", "checkout prompt"])
        args = orch.parse_args()
        assert args.command == ["new", "billing/Checkout", "checkout prompt"]
        assert args.no_controller is False
        assert args.max_attempts == 0

    def test_parse_args_options(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        model_config = tmp_path / "model_config.json"
        monkeypatch.setattr(orch, "MODEL_CONFIG", model_config)
        monkeypatch.setattr(
            sys, "argv",
            ["orch.py", "do", "Checkout", "--no-controller", "--max-attempts", "5", "--model", "custom-model", "-a", "private"],
        )
        args = orch.parse_args()
        assert args.command == ["do", "Checkout"]
        assert args.no_controller is True
        assert args.max_attempts == 5
        assert args.app == "private"
        assert model_config.exists()
        assert json.loads(model_config.read_text()) == {"model": "custom-model"}

    def test_parse_args_no_command_exits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "argv", ["orch.py"])
        with pytest.raises(SystemExit) as exc_info:
            orch.parse_args()
        assert exc_info.value.code == 1
