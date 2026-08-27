import json
from pathlib import Path
from unittest.mock import patch

from _orchestrator.feature import (
    ProjectFeatures,
    discover_config,
    feature_from_branch,
    feature_name_from_path,
    load_project,
    register_target,
    unregister_feature,
)


def _project(tmp_path: Path, config: dict | None = None, features: dict[str, str] | None = None) -> ProjectFeatures:
    cfg = config or {"known_features": features or {}, "domain_keywords": {}}
    (tmp_path / ".features.json").write_text(json.dumps(cfg) + "\n")
    return ProjectFeatures.load(tmp_path)


class TestDiscoverConfig:

    def test_root_config_found(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".features.json"
        cfg.touch()
        assert discover_config(tmp_path) == cfg

    def test_nested_config_found_when_root_missing(self, tmp_path: Path) -> None:
        nested = tmp_path / "web" / "features"
        nested.mkdir(parents=True)
        cfg = nested / ".features.json"
        cfg.touch()
        assert discover_config(tmp_path) == cfg

    def test_defaults_to_root_when_missing(self, tmp_path: Path) -> None:
        assert discover_config(tmp_path) == tmp_path / ".features.json"


class TestProjectLoad:

    def test_apps_and_domains(self, tmp_path: Path) -> None:
        cfg = {
            "features_dir": "web/features",
            "apps": {
                "web": {"features_dir": "web/features", "config": "web/features/.features.json", "domains": ["vps", "nodomain"]},
                "admin": {"features_dir": "features", "config": "features/.features.json", "domains": ["dashboard"]},
            },
        }
        (tmp_path / "web" / "features").mkdir(parents=True)
        (tmp_path / "web" / "features" / ".features.json").write_text(json.dumps({"known_features": {"Payment": "vps"}}))
        (tmp_path / "features").mkdir()
        (tmp_path / "features" / ".features.json").write_text(json.dumps({}))
        project = _project(tmp_path, cfg)
        assert project.features_root == tmp_path / "web" / "features"
        assert project.domains() == ["dashboard", "nodomain", "vps"]
        assert project.app_for_domain("vps") == "web"
        assert project.app_for_domain("dashboard") == "admin"
        assert project.app_for_domain("unknown") == ""
        assert project.root_for_domain("dashboard") == tmp_path / "features"
        assert project.known_features == {"Payment": "vps"}


class TestInferDomain:

    def test_known_feature(self, tmp_path: Path) -> None:
        project = _project(tmp_path, features={"TrackHoldings": "state"})
        assert project.infer_domain("TrackHoldings") == "state"

    def test_keyword(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        project.domain_keywords["candle"] = ("candle", "ManageCandle")
        assert project.infer_domain("candle") == "candle"
        assert project.infer_domain("ManageCandle") == "candle"

    def test_unknown(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        assert project.infer_domain("NonexistentFeature") == ""


class TestTargetForNew:

    def test_bare_name_lands_in_nodomain(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        target = project.target_for_new("Payments", "")
        assert target.domain == "nodomain"
        assert target.dir == tmp_path / "features" / "nodomain" / "Payments"
        assert target.config_path == tmp_path / ".features.json"

    def test_explicit_domain(self, tmp_path: Path) -> None:
        project = _project(tmp_path, features={"Payments": "shared"})
        target = project.target_for_new("Payments", "shared")
        assert target.dir == tmp_path / "features" / "shared" / "Payments"

    def test_inferred_domain(self, tmp_path: Path) -> None:
        project = _project(tmp_path, features={"Payments": "vps"})
        target = project.target_for_new("Payments", "")
        assert target.domain == "vps"
        assert target.dir == tmp_path / "features" / "vps" / "Payments"


class TestResolve:

    def test_finds_nested_feature(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        target = _project(tmp_path).resolve("RunRatchetStrategy")
        assert target is not None
        assert target.name == "RunRatchetStrategy"
        assert target.domain == "strategy"

    def test_finds_flat_feature(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "state").mkdir(parents=True)
        (tmp_path / "features" / "state" / "Handler.py").touch()
        target = _project(tmp_path).resolve("state")
        assert target is not None
        assert target.name == "state"
        assert target.domain == "nodomain"

    def test_finds_by_lowercase(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        target = _project(tmp_path).resolve("runratchetstrategy")
        assert target is not None
        assert target.name == "RunRatchetStrategy"

    def test_known_feature_lookup(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "state" / "TrackHoldings").mkdir(parents=True)
        project = _project(tmp_path, features={"TrackHoldings": "state"})
        target = project.resolve("TrackHoldings")
        assert target is not None
        assert target.dir == tmp_path / "features" / "state" / "TrackHoldings"

    def test_returns_none_for_unknown(self, tmp_path: Path) -> None:
        assert _project(tmp_path).resolve("NoSuchFeature") is None

    def test_empty_returns_none(self, tmp_path: Path) -> None:
        assert _project(tmp_path).resolve("") is None


class TestFuzzySuggest:

    def test_suggests_close_match(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        (tmp_path / "features" / "candle" / "ManageCandle").mkdir(parents=True)
        with patch("builtins.input", return_value="1"):
            target = _project(tmp_path).resolve("RunRatchet")
        assert target is not None
        assert target.name == "RunRatchetStrategy"

    def test_cancel_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        with patch("builtins.input", return_value="n"):
            target = _project(tmp_path).resolve("RunRatchet")
        assert target is None


class TestRegisterUnregister:

    def test_registers_new_feature(self, tmp_path: Path) -> None:
        project = _project(tmp_path)
        target = project.target_for_new("ManageCandle", "candle")
        register_target(target)
        data = json.loads((tmp_path / ".features.json").read_text())
        assert data["known_features"]["ManageCandle"] == "candle"
        assert data["domain_keywords"]["managecandle"] == ["candle", "ManageCandle"]

    def test_skips_duplicate(self, tmp_path: Path) -> None:
        project = _project(tmp_path, features={"ManageCandle": "candle"})
        target = project.target_for_new("ManageCandle", "candle")
        register_target(target)
        data = json.loads((tmp_path / ".features.json").read_text())
        assert data["known_features"]["ManageCandle"] == "candle"

    def test_unregisters_feature_and_keywords(self, tmp_path: Path) -> None:
        _project(tmp_path, features={"ManageCandle": "candle"})
        cfg = tmp_path / ".features.json"
        data = json.loads(cfg.read_text())
        data["domain_keywords"] = {"candle": ["candle", "ManageCandle"]}
        cfg.write_text(json.dumps(data) + "\n")
        assert unregister_feature("ManageCandle", config_path=cfg)
        data = json.loads(cfg.read_text())
        assert "ManageCandle" not in data["known_features"]
        assert "candle" not in data["domain_keywords"]

    def test_unregister_unknown_returns_false(self, tmp_path: Path) -> None:
        _project(tmp_path)
        assert not unregister_feature("NoSuchFeature", config_path=tmp_path / ".features.json")


class TestScan:

    def test_scans_nested_and_flat(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "JournalTrades").mkdir(parents=True)
        (tmp_path / "features" / "JournalTrades" / "Handler.py").touch()
        (tmp_path / "features" / "strategy" / "Ratchet").mkdir(parents=True)
        project = _project(tmp_path)
        assert project.scan_map() == {"JournalTrades": "nodomain", "Ratchet": "strategy"}

    def test_skips_underscore_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "_private" / "Secret").mkdir(parents=True)
        assert _project(tmp_path).scan_map() == {}


class TestNameFromInput:

    def test_known_feature_by_name(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        assert _project(tmp_path).name_from_input("RunRatchetStrategy") == "RunRatchetStrategy"

    def test_domain_slash_feature(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        assert _project(tmp_path).name_from_input("strategy/RunRatchetStrategy") == "RunRatchetStrategy"

    def test_full_path(self, tmp_path: Path) -> None:
        feature_dir = tmp_path / "features" / "strategy" / "RunRatchetStrategy"
        feature_dir.mkdir(parents=True)
        (feature_dir / "Handler.py").touch()
        file_path = str(feature_dir / "Handler.py")
        assert _project(tmp_path).name_from_input(file_path) == "RunRatchetStrategy"

    def test_new_feature_name_returns_as_is(self, tmp_path: Path) -> None:
        assert _project(tmp_path).name_from_input("BrandNewFeature") == "BrandNewFeature"

    def test_empty_input(self, tmp_path: Path) -> None:
        assert _project(tmp_path).name_from_input("") == ""

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "strategy" / "RunRatchetStrategy").mkdir(parents=True)
        assert _project(tmp_path).name_from_input("runratchetstrategy") == "RunRatchetStrategy"

    def test_nonexistent_path_returns_basename(self, tmp_path: Path) -> None:
        assert _project(tmp_path).name_from_input("/nonexistent/path/MyFeature") == "MyFeature"


class TestNameFromPath:

    def test_extracts_from_features_subpath(self, tmp_path: Path) -> None:
        feature_dir = tmp_path / "features" / "strategy" / "RunRatchetStrategy"
        feature_dir.mkdir(parents=True)
        (feature_dir / "Handler.py").touch()
        result = _project(tmp_path).name_from_path(str(feature_dir / "Handler.py"))
        assert result == "RunRatchetStrategy"

    def test_returns_none_for_nonexistent_path(self, tmp_path: Path) -> None:
        assert _project(tmp_path).name_from_path("/nonexistent/path") is None


class TestResolveModify:

    def test_explicit_existing_feature(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "shared" / "Payment").mkdir(parents=True)
        res = _project(tmp_path).resolve_modify("Payment")
        assert res is not None
        assert res.name == "Payment"
        assert res.amend is not None
        assert res.amend.dir == tmp_path / "features" / "shared" / "Payment"
        assert res.branch_domain == "shared"

    def test_explicit_unknown_name(self, tmp_path: Path) -> None:
        res = _project(tmp_path).resolve_modify("BrandNew")
        assert res is not None
        assert res.amend is None
        assert res.name == "BrandNew"

    def test_implicit_file_creates_nodomain_copy(self, tmp_path: Path) -> None:
        (tmp_path / "features" / "shared" / "Auctions").mkdir(parents=True)
        project = _project(tmp_path)
        res = project.resolve_modify("/repo/web/auctions.html", implicit=True)
        assert res is not None
        assert res.name == "Auctions"
        assert res.amend is not None
        assert res.amend.domain == "nodomain"
        assert res.amend.dir == tmp_path / "features" / "nodomain" / "Auctions"
        assert res.branch_domain == "shared"

    def test_implicit_new_feature(self, tmp_path: Path) -> None:
        res = _project(tmp_path).resolve_modify("/repo/web/dashboard.html", implicit=True)
        assert res is not None
        assert res.amend is not None
        assert res.amend.domain == "nodomain"
        assert res.branch_domain == "nodomain"

    def test_empty_returns_none(self, tmp_path: Path) -> None:
        assert _project(tmp_path).resolve_modify("") is None


class TestFeatureFromBranch:

    def test_feature_prefix(self) -> None:
        assert feature_from_branch("feature/Payment") == "Payment"

    def test_modify_prefix(self) -> None:
        assert feature_from_branch("modify/Payment") == "Payment"

    def test_domain_slash_feature_branch(self) -> None:
        assert feature_from_branch("shared/Payment") == "Payment"

    def test_main_returns_empty(self) -> None:
        assert feature_from_branch("main") == ""

    def test_plain_branch_returns_branch_name(self) -> None:
        assert feature_from_branch("dev") == "dev"


class TestFeatureNameFromPath:

    def test_derives_camel_case_from_file(self) -> None:
        assert feature_name_from_path("web/auctions.html") == "Auctions"

    def test_derives_from_underscored(self) -> None:
        assert feature_name_from_path("payment_flow.py") == "PaymentFlow"

    def test_derives_two_words(self) -> None:
        assert feature_name_from_path("share screenshot separately") == "ShareScreenshot"

    def test_derives_single_word(self) -> None:
        assert feature_name_from_path("payment") == "Payment"


class TestLoadProject:

    def test_load_reads_config_and_root(self, tmp_path: Path) -> None:
        cfg = tmp_path / ".features.json"
        cfg.write_text(json.dumps({"features_dir": "src/features"}) + "\n")
        project = load_project(tmp_path)
        assert project.config_path == cfg
        assert project.features_root == tmp_path / "src" / "features"
