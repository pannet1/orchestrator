"""Project-aware feature resolution.

Resolves the current project's feature structure: where the feature config
(.features.json) lives, which domains exist, and where a feature folder is.
The location of the config varies per project (repo root or nested inside a
features dir), so nothing here assumes a fixed path.

Commands receive resolved FeatureTargets — they operate on paths, they do not
resolve features themselves.
"""
import difflib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import REPO_ROOT

MAX_CONFIG_DEPTH = 5

EXCLUDED_DIRS: frozenset[str] = frozenset({
    ".venv", "venv", "env", "node_modules", "__pycache__",
    ".git", ".hg", ".svn", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".agents",
})


@dataclass(frozen=True)
class FeatureTarget:
    """A resolved feature location. Commands operate on `dir`; they never resolve."""
    name: str
    domain: str
    dir: Path
    root: Path
    config_path: Path
    app: str = ""


@dataclass(frozen=True)
class ModifyResolution:
    """Modify's resolution: where to amend, which domain governs the branch."""
    name: str
    amend: FeatureTarget | None
    branch_domain: str = ""
    scaffold_overview: str = ""


@dataclass(frozen=True)
class AppConfig:
    key: str
    features_dir: Path
    config_path: Path
    domains: tuple[str, ...]


@dataclass
class ProjectFeatures:
    repo_root: Path
    config_path: Path
    features_root: Path
    apps: dict[str, AppConfig] = field(default_factory=dict)
    known_features: dict[str, str] = field(default_factory=dict)
    domain_keywords: dict[str, tuple[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, repo_root: Path) -> "ProjectFeatures":
        config_path = discover_config(repo_root)
        cfg: dict[str, Any] = {}
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text())
            except (json.JSONDecodeError, OSError):
                cfg = {}
        default_dir_name = str(cfg.get("features_dir", "features"))
        features_root = repo_root / default_dir_name

        apps: dict[str, AppConfig] = {}
        for key, app_cfg in cfg.get("apps", {}).items():
            if not isinstance(app_cfg, dict):
                continue
            adir = repo_root / str(app_cfg.get("features_dir", default_dir_name))
            acfg = repo_root / str(app_cfg["config"]) if app_cfg.get("config") else config_path
            apps[str(key)] = AppConfig(
                key=str(key),
                features_dir=adir,
                config_path=acfg,
                domains=tuple(str(d) for d in app_cfg.get("domains", [])),
            )

        known: dict[str, str] = dict(cfg.get("known_features", {}))
        keywords: dict[str, tuple[str, str]] = {}
        for k, v in cfg.get("domain_keywords", {}).items():
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                keywords[str(k)] = (str(v[0]), str(v[1]))
        for app_cfg in apps.values():
            if app_cfg.config_path == config_path or not app_cfg.config_path.exists():
                continue
            try:
                data = json.loads(app_cfg.config_path.read_text())
            except (json.JSONDecodeError, OSError):
                data = {}
            known.update(data.get("known_features", {}))
            for k, v in data.get("domain_keywords", {}).items():
                if isinstance(v, (list, tuple)) and len(v) >= 2:
                    keywords[str(k)] = (str(v[0]), str(v[1]))

        return cls(
            repo_root=repo_root,
            config_path=config_path,
            features_root=features_root,
            apps=apps,
            known_features=known,
            domain_keywords=keywords,
        )

    # -- structure ----------------------------------------------------------

    def roots(self, app: str = "") -> list[Path]:
        if app and app in self.apps:
            return [self.apps[app].features_dir]
        roots_list: list[Path] = [self.features_root]
        for a in self.apps.values():
            if a.features_dir not in roots_list:
                roots_list.append(a.features_dir)
        return roots_list

    def domains(self) -> list[str]:
        merged: list[str] = []
        for a in self.apps.values():
            merged.extend(a.domains)
        return sorted(set(merged))

    def root_for_domain(self, domain: str) -> Path:
        for a in self.apps.values():
            if domain in a.domains:
                return a.features_dir
        return self.features_root

    def config_for_domain(self, domain: str) -> Path:
        for a in self.apps.values():
            if domain in a.domains:
                return a.config_path
        return self.config_path

    def app_for_domain(self, domain: str) -> str:
        for a in self.apps.values():
            if domain in a.domains:
                return a.key
        return ""

    def _root_of(self, feature_dir: Path) -> Path:
        resolved = feature_dir.resolve()
        best = self.features_root
        best_len = -1
        for r in self.roots():
            rr = r.resolve()
            try:
                resolved.relative_to(rr)
            except ValueError:
                continue
            if len(rr.parts) > best_len:
                best, best_len = r, len(rr.parts)
        return best

    def _target_for_dir(self, feature_dir: Path) -> FeatureTarget:
        root = self._root_of(feature_dir)
        app = next((a.key for a in self.apps.values() if a.features_dir == root), "")
        config = self.apps[app].config_path if app else self.config_path
        return FeatureTarget(
            name=feature_dir.name,
            domain=self.domain_of(feature_dir),
            dir=feature_dir,
            root=root,
            config_path=config,
            app=app,
        )

    # -- resolution ----------------------------------------------------------

    def infer_domain(self, name: str) -> str:
        if name in self.known_features:
            return self.known_features[name]
        lower = name.lower()
        for key, (domain, action) in self.domain_keywords.items():
            if lower == key or lower == action.lower():
                return domain
        return ""

    def target_for_new(self, name: str, domain: str = "", app: str = "") -> FeatureTarget:
        domain = domain or self.infer_domain(name) or "nodomain"
        if app and app in self.apps:
            root, config = self.apps[app].features_dir, self.apps[app].config_path
        else:
            root, config = self.root_for_domain(domain), self.config_for_domain(domain)
        return FeatureTarget(
            name=name,
            domain=domain,
            dir=root / domain / name,
            root=root,
            config_path=config,
            app=app,
        )

    def resolve(self, raw: str, app: str = "") -> FeatureTarget | None:
        if not raw:
            return None
        found = self._find_dir(raw, app=app)
        if found:
            return self._target_for_dir(found)
        suggested = self._fuzzy_suggest(raw, app=app)
        if suggested:
            return self._target_for_dir(suggested)
        return None

    def resolve_exact(self, raw: str, app: str = "") -> FeatureTarget | None:
        if not raw:
            return None
        found = self._find_dir(raw, app=app)
        if found:
            return self._target_for_dir(found)
        return None

    def _find_dir(self, raw: str, app: str = "") -> Path | None:
        lower = raw.lower()
        if raw in self.known_features:
            found = self._dir_for_known(raw, self.known_features[raw], app=app)
            if found:
                return found
        for base_dir in self.roots(app=app):
            result = self._find_in_dir(base_dir, lower)
            if result:
                return result
        return None

    def _dir_for_known(self, name: str, domain: str, app: str = "") -> Path | None:
        if app and app in self.apps:
            fdir = self.apps[app].features_dir
        else:
            fdir = self.root_for_domain(domain)
        domain_dir = fdir / domain if domain else fdir
        if domain_dir.is_dir():
            if (domain_dir / "Handler.py").exists():
                return domain_dir
            feature_dir = domain_dir / name
            if feature_dir.is_dir():
                return feature_dir
        flat_dir = fdir / name
        if flat_dir.is_dir() and (flat_dir / "Handler.py").exists():
            return flat_dir
        return None

    def _find_in_dir(self, base_dir: Path, lower: str) -> Path | None:
        if not base_dir.is_dir():
            return None
        for domain_dir in base_dir.iterdir():
            if not domain_dir.is_dir() or domain_dir.name.startswith("_"):
                continue
            if domain_dir.name.lower() == lower and (domain_dir / "Handler.py").exists():
                return domain_dir
            for entry in domain_dir.iterdir():
                if entry.is_dir() and not entry.name.startswith("_") and entry.name.lower() == lower:
                    return entry
        return None

    def _fuzzy_suggest(self, raw: str, app: str = "") -> Path | None:
        candidates: dict[str, Path] = {}
        for base_dir in self.roots(app=app):
            if not base_dir.is_dir():
                continue
            for domain_dir in base_dir.iterdir():
                if domain_dir.is_dir() and not domain_dir.name.startswith("_"):
                    candidates[domain_dir.name] = domain_dir
                    if (domain_dir / "Handler.py").exists():
                        continue
                    for entry in domain_dir.iterdir():
                        if entry.is_dir() and not entry.name.startswith("_"):
                            candidates[entry.name] = entry

        request_lower = raw.lower()
        prefix_matches: list[str] = []
        for cname in candidates:
            clower = cname.lower()
            if clower.startswith(request_lower) or request_lower.startswith(clower):
                prefix_matches.append(cname)
        matches = difflib.get_close_matches(raw, list(candidates.keys()), n=5, cutoff=0.4)
        seen: set[str] = set()
        combined: list[str] = []
        for m in prefix_matches + matches:
            if m not in seen:
                seen.add(m)
                combined.append(m)
        matches = combined[:3]
        if not matches:
            return None

        print(f"[Orchestrator] No exact match for '{raw}'. Did you mean:")
        for i, m in enumerate(matches, 1):
            print(f"  {i}. {m}")
        print("  n. No, cancel")
        try:
            choice = input(f"Enter choice [1-{len(matches)} or n]: ").strip().lower()
        except EOFError:
            choice = "n"
        if choice == "n":
            return None
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(matches):
                return candidates[matches[idx]]
        except ValueError:
            pass
        return None

    def name_from_input(self, raw: str) -> str:
        cleaned = raw.strip().strip("/")
        existing = self.resolve_exact(cleaned)
        if existing:
            return existing.name
        parts = cleaned.split("/")
        for i in range(1, len(parts)):
            candidate = parts[-i]
            existing = self.resolve_exact(candidate)
            if existing:
                return existing.name
        path_feature = self.name_from_path(cleaned)
        if path_feature:
            return path_feature
        for base_dir in self.roots():
            if not base_dir.is_dir():
                continue
            for domain_dir in base_dir.iterdir():
                if not domain_dir.is_dir() or domain_dir.name.startswith("_"):
                    continue
                for entry in domain_dir.iterdir():
                    if entry.is_dir() and not entry.name.startswith("_") and entry.name.lower() == cleaned.lower():
                        return entry.name
        return Path(cleaned).name if "/" in cleaned else cleaned

    def name_from_path(self, path_str: str) -> str | None:
        resolved = self.repo_root / path_str
        if not resolved.exists():
            return None
        parts = resolved.resolve().parts
        for base_dir in self.roots():
            rp = base_dir.resolve().parts
            if len(parts) < len(rp) or parts[: len(rp)] != tuple(rp):
                continue
            remainder = parts[len(rp):]
            if len(remainder) >= 2:
                domain, feature_name = remainder[0], remainder[1]
                if (base_dir / domain / feature_name).is_dir():
                    return feature_name
            if remainder:
                last = remainder[-1]
                if self.known_features.get(last) or self.resolve_exact(last):
                    return last
        candidate = resolved.stem if resolved.suffix else resolved.name
        if self.resolve_exact(candidate):
            return candidate
        return None

    def resolve_modify(self, raw: str, app: str = "", implicit: bool = False) -> ModifyResolution | None:
        """Map modify's input to a ModifyResolution.

        implicit=True: input is a file the user is editing — an existing feature
        is never amended in place; a nodomain copy is created instead.
        """
        if not raw:
            return None
        if implicit:
            name = feature_name_from_path(raw)
            if not name:
                return None
            existing = self.resolve_exact(name, app=app)
            if existing:
                new = self.target_for_new(name, "", app)
                return ModifyResolution(
                    name=name, amend=new, branch_domain=existing.domain,
                    scaffold_overview=f"Modify {raw}",
                )
            fuzzy = self._fuzzy_suggest(name, app=app)
            if fuzzy:
                ft = self._target_for_dir(fuzzy)
                return ModifyResolution(name=name, amend=ft, branch_domain=ft.domain)
            new = self.target_for_new(name, "", app)
            return ModifyResolution(name=name, amend=new, branch_domain=new.domain)
        existing = self.resolve(raw, app=app)
        if existing:
            return ModifyResolution(name=existing.name, amend=existing, branch_domain=existing.domain)
        p = self.repo_root / raw
        if p.exists() and p.is_file():
            name = feature_name_from_path(raw)
            if not name:
                return None
            by_name = self.resolve_exact(name, app=app)
            if by_name:
                new = self.target_for_new(name, "", app)
                return ModifyResolution(
                    name=name, amend=new, branch_domain=by_name.domain,
                    scaffold_overview=f"Modify {raw}",
                )
            return ModifyResolution(name=name, amend=None)
        name = self.name_from_input(raw)
        return ModifyResolution(name=name, amend=None)

    def domain_of(self, feature_dir: Path | None) -> str:
        if not feature_dir:
            return ""
        for r in self.roots():
            if feature_dir.parent == r:
                return "nodomain"
        return feature_dir.parent.name

    # -- registry ------------------------------------------------------------

    def scan(self) -> list[FeatureTarget]:
        out: list[FeatureTarget] = []
        for base_dir in self.roots():
            if not base_dir.is_dir():
                continue
            for domain_dir in base_dir.iterdir():
                if not domain_dir.is_dir() or domain_dir.name.startswith("_"):
                    continue
                if (domain_dir / "Handler.py").exists():
                    out.append(self._target_for_dir(domain_dir))
                    continue
                for entry in domain_dir.iterdir():
                    if entry.is_dir() and not entry.name.startswith("_"):
                        out.append(self._target_for_dir(entry))
        return out

    def scan_map(self) -> dict[str, str]:
        return {t.name: t.domain for t in self.scan()}


def load_project(repo_root: Path = REPO_ROOT) -> ProjectFeatures:
    return ProjectFeatures.load(repo_root)


def discover_config(repo_root: Path) -> Path:
    """Find the project's .features.json. Root first, then a bounded nested search."""
    root_cfg = repo_root / ".features.json"
    if root_cfg.exists():
        return root_cfg
    for dirpath, dirnames, filenames in os.walk(repo_root):
        depth = len(Path(dirpath).relative_to(repo_root).parts)
        if depth >= MAX_CONFIG_DEPTH:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        if ".features.json" in filenames:
            return Path(dirpath) / ".features.json"
    return root_cfg


def feature_from_branch(branch: str) -> str:
    if not branch or branch == "(unknown)" or branch == "main" or branch.startswith("main"):
        return ""
    return branch.rsplit("/", 1)[-1]


def feature_name_from_path(path_str: str) -> str:
    clean = path_str.strip().strip('"').strip("'")
    m = re.search(r"features/([A-Za-z0-9_-]+)/([A-Za-z0-9_-]+)", clean)
    if m:
        return m.group(2)
    p = Path(clean)
    stem = p.stem
    if re.fullmatch(r"[A-Za-z0-9_./\\-]+", clean):
        name = "".join(word.capitalize() for word in stem.replace("-", "_").split("_"))
        if name:
            return name
        parent_name = "".join(word.capitalize() for word in p.parent.name.replace("-", "_").split("_"))
        if parent_name:
            return parent_name
    words = re.findall(r"[A-Za-z]+", clean)
    stopwords = {"the", "a", "an", "from", "into", "with", "for", "to", "in", "of", "and", "or", "is", "as", "by", "on", "at"}
    significant = [w for w in words if w.lower() not in stopwords]
    if len(significant) >= 2:
        return significant[0].capitalize() + significant[1].capitalize()
    if significant:
        return significant[0].capitalize() + "Feature"
    return "Feature"


def register_target(target: FeatureTarget) -> None:
    cfg_path = target.config_path
    features = (json.loads(cfg_path.read_text()) if cfg_path.exists()
                else {"known_features": {}, "domain_keywords": {}})
    known = features.setdefault("known_features", {})
    if target.name not in known:
        known[target.name] = target.domain
        keyword = target.name.lower().replace("feature", "").replace("handler", "").replace("controller", "")
        keywords = features.setdefault("domain_keywords", {})
        if keyword and keyword not in keywords:
            keywords[keyword] = [target.domain, target.name]
        cfg_path.write_text(json.dumps(features, indent=2) + "\n")
        print(f"[Orchestrator] Registered '{target.name}' -> '{target.domain}' in {cfg_path.name}")


def unregister_feature(name: str, feature_dir: Path | None = None, config_path: Path | None = None) -> bool:
    cfg_path = config_path or load_project().config_path
    if not cfg_path.exists():
        return False
    features = json.loads(cfg_path.read_text())
    known = features.get("known_features", {})
    keywords = features.get("domain_keywords", {})

    candidates = [name]
    if feature_dir:
        candidates.append(feature_dir.name)
    removed = False
    for candidate in candidates:
        if candidate in known:
            del known[candidate]
            removed = True
            stale = [k for k, v in keywords.items() if len(v) >= 2 and v[1] == candidate]
            for k in stale:
                del keywords[k]
    if removed:
        features["known_features"] = known
        features["domain_keywords"] = keywords
        cfg_path.write_text(json.dumps(features, indent=2) + "\n")
        print(f"[Orchestrator] Unregistered '{name}' from {cfg_path.name}")
    return removed


FEATURE_SCAN_DIRS = frozenset({
    "scripts", "health", "web", "migrations", "private",
    "db", "common", "tests", "factory",
})


def detect_features_dir(repo_root: Path) -> str:
    candidates = [
        "apps/backend/app/features",
        "backend/src/features",
        "backend/app/features",
        "src/features",
        "app/features",
        "features",
    ]
    for path in candidates:
        if (repo_root / path).is_dir():
            return path
    return "features"


def detect_existing_features(repo_root: Path) -> dict[str, str]:
    """Scan project directories for existing script/feature files."""
    features: dict[str, str] = {}
    for dirname in FEATURE_SCAN_DIRS:
        d = repo_root / dirname
        if not d.is_dir():
            continue
        for fp in sorted(d.rglob("*.py")):
            fname = fp.stem
            if fname.startswith("_"):
                continue
            if any(p.startswith(".") or p == "__pycache__" for p in fp.parts):
                continue
            if fname not in features:
                features[fname] = dirname
    return features
