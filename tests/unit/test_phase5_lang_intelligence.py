"""
Unit tests for Phase 5: Language Intelligence

Covers:
  - stack_detector.detect(): config file parsing for various languages
  - lang-rules files: existence and version section structure
  - review_job._build_lang_rules_section(): auto-injection of rules
  - file_filter registry mode: .cs reviewed, .json skipped
"""

import json
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

_src = Path(__file__).parent.parent.parent / "src"
_commands = Path(__file__).parent.parent.parent / "commands"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

import stack_detector as sd
from stack_detector import StackProfile, detect
from file_filter import filter_changed_files, parse_skip_extensions


@dataclass
class FakeFC:
    path: str
    change_type: str = "edit"
    additions: int = 0
    deletions: int = 0


# ---------------------------------------------------------------------------
# Stack detector tests
# ---------------------------------------------------------------------------

class TestStackDetector:

    def test_detect_csharp_net8(self, tmp_path):
        csproj = tmp_path / "MyApp.csproj"
        csproj.write_text(
            "<Project><PropertyGroup>"
            "<TargetFramework>net8.0</TargetFramework>"
            "</PropertyGroup></Project>",
            encoding="utf-8",
        )
        profile = detect(tmp_path)
        assert "csharp" in profile.languages
        assert profile.frameworks["csharp"]["dotnet"] == "8.0"
        assert "MyApp.csproj" in profile.detected_from

    def test_detect_csharp_net6(self, tmp_path):
        csproj = tmp_path / "App.csproj"
        csproj.write_text(
            "<Project><PropertyGroup>"
            "<TargetFramework>net6.0</TargetFramework>"
            "</PropertyGroup></Project>",
            encoding="utf-8",
        )
        profile = detect(tmp_path)
        assert profile.frameworks["csharp"]["dotnet"] == "6.0"

    def test_detect_csharp_ef_core_version(self, tmp_path):
        csproj = tmp_path / "App.csproj"
        csproj.write_text(
            '<Project><PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>'
            '<ItemGroup>'
            '<PackageReference Include="Microsoft.EntityFrameworkCore" Version="8.0.1" />'
            '</ItemGroup></Project>',
            encoding="utf-8",
        )
        profile = detect(tmp_path)
        assert profile.frameworks["csharp"].get("efcore") == "8.0.1"

    def test_detect_typescript_from_package_json(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps({
                "dependencies": {"react": "^18.2.0"},
                "devDependencies": {"typescript": "^5.3.0"},
            }),
            encoding="utf-8",
        )
        (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
        profile = detect(tmp_path)
        assert "typescript" in profile.languages
        assert "react" in profile.languages
        assert profile.frameworks["typescript"]["typescript"] == "5.3.0"
        assert profile.frameworks["typescript"]["react"] == "18.2.0"

    def test_detect_react_added_as_overlay(self, tmp_path):
        pkg = tmp_path / "package.json"
        pkg.write_text(
            json.dumps({"dependencies": {"react": "^17.0.0"}}),
            encoding="utf-8",
        )
        profile = detect(tmp_path)
        assert "react" in profile.languages

    def test_detect_python_from_requirements_txt(self, tmp_path):
        req = tmp_path / "requirements.txt"
        req.write_text("django>=4.2\nrequests>=2.28\n", encoding="utf-8")
        profile = detect(tmp_path)
        assert "python" in profile.languages
        assert "requirements.txt" in profile.detected_from

    def test_detect_python_version_from_pyproject(self, tmp_path):
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[tool.poetry]\nname = "app"\n[tool.poetry.dependencies]\npython = ">=3.10"\n',
            encoding="utf-8",
        )
        profile = detect(tmp_path)
        assert "python" in profile.languages
        assert profile.frameworks["python"].get("python") == ">=3.10"

    def test_detect_go(self, tmp_path):
        gomod = tmp_path / "go.mod"
        gomod.write_text("module github.com/example/app\n\ngo 1.21\n", encoding="utf-8")
        profile = detect(tmp_path)
        assert "go" in profile.languages
        assert profile.frameworks["go"]["go"] == "1.21"

    def test_detect_rust_edition(self, tmp_path):
        cargo = tmp_path / "Cargo.toml"
        cargo.write_text('[package]\nname = "app"\nedition = "2021"\n', encoding="utf-8")
        profile = detect(tmp_path)
        assert "rust" in profile.languages
        assert profile.frameworks["rust"]["edition"] == "2021"

    def test_detect_empty_workspace(self, tmp_path):
        profile = detect(tmp_path)
        assert profile.languages == []
        assert profile.detected_from == []

    def test_profile_dataclass_defaults(self):
        p = StackProfile()
        assert p.languages == []
        assert p.frameworks == {}
        assert p.detected_from == []


# ---------------------------------------------------------------------------
# Lang-rules files existence tests
# ---------------------------------------------------------------------------

class TestLangRulesFiles:
    EXPECTED_FILES = [
        "csharp", "javascript", "typescript", "react", "python",
        "java", "kotlin", "go", "rust", "cpp", "swift", "android",
        "ruby", "php", "sql",
    ]

    def test_all_15_rules_files_exist(self):
        lang_rules_dir = _commands / "lang-rules"
        for lang in self.EXPECTED_FILES:
            rules_file = lang_rules_dir / f"{lang}.md"
            assert rules_file.is_file(), f"Missing lang-rules file: {rules_file}"

    def test_each_rules_file_has_checklist_items(self):
        lang_rules_dir = _commands / "lang-rules"
        for lang in self.EXPECTED_FILES:
            rules_file = lang_rules_dir / f"{lang}.md"
            if not rules_file.is_file():
                continue
            content = rules_file.read_text(encoding="utf-8")
            checklist_count = content.count("- [ ]")
            assert checklist_count >= 30, (
                f"{lang}.md has only {checklist_count} checklist items (expected >= 30)"
            )

    def test_each_rules_file_has_version_sections(self):
        lang_rules_dir = _commands / "lang-rules"
        for lang in self.EXPECTED_FILES:
            rules_file = lang_rules_dir / f"{lang}.md"
            if not rules_file.is_file():
                continue
            content = rules_file.read_text(encoding="utf-8")
            # Must have at least one ## heading (version section)
            assert "## " in content, f"{lang}.md has no version section headings"

    def test_languages_yml_has_15_entries(self):
        yml_path = _commands / "languages.yml"
        assert yml_path.is_file(), "commands/languages.yml not found"
        import yaml
        data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        langs = data.get("languages", {})
        assert len(langs) == 15, f"Expected 15 languages, got {len(langs)}"

    def test_languages_yml_entries_have_required_fields(self):
        yml_path = _commands / "languages.yml"
        if not yml_path.is_file():
            pytest.skip("languages.yml not found")
        import yaml
        data = yaml.safe_load(yml_path.read_text(encoding="utf-8"))
        for name, cfg in data.get("languages", {}).items():
            assert "extensions" in cfg, f"{name} missing 'extensions'"
            assert "config_files" in cfg, f"{name} missing 'config_files'"
            assert "rules_file" in cfg, f"{name} missing 'rules_file'"


# ---------------------------------------------------------------------------
# Version filtering tests
# ---------------------------------------------------------------------------

class TestVersionFiltering:

    def _filter(self, rules_text: str, lang: str, frameworks: dict) -> str:
        from review_job import _filter_rules_for_version
        return _filter_rules_for_version(rules_text, lang, frameworks)

    def test_all_versions_section_always_included(self):
        rules = "# C# Rules\n\n## All Versions\n\n- [ ] Item A\n\n## .NET 8+\n\n- [ ] Item B\n"
        result = self._filter(rules, "csharp", {"dotnet": "6.0"})
        assert "Item A" in result
        assert "Item B" not in result

    def test_net8_section_included_for_net8(self):
        rules = "# C# Rules\n\n## All Versions\n\n- [ ] Item A\n\n## .NET 8+\n\n- [ ] Item B\n"
        result = self._filter(rules, "csharp", {"dotnet": "8.0"})
        assert "Item A" in result
        assert "Item B" in result

    def test_net8_section_excluded_for_net6(self):
        rules = "# C# Rules\n\n## All Versions\n\n- [ ] Item A\n\n## .NET 8+\n\n- [ ] Item B\n"
        result = self._filter(rules, "csharp", {"dotnet": "6.0"})
        assert "Item B" not in result

    def test_python_310_section_included_for_312(self):
        rules = "# Python Rules\n\n## All Versions\n\n- [ ] Item A\n\n## Python 3.10+\n\n- [ ] Item B\n"
        result = self._filter(rules, "python", {"python": "3.12"})
        assert "Item B" in result

    def test_python_310_section_excluded_for_38(self):
        rules = "# Python Rules\n\n## All Versions\n\n- [ ] Item A\n\n## Python 3.10+\n\n- [ ] Item B\n"
        result = self._filter(rules, "python", {"python": "3.8"})
        assert "Item B" not in result

    def test_unknown_version_includes_section(self):
        rules = "# Rules\n\n## .NET 8+\n\n- [ ] Item B\n"
        result = self._filter(rules, "csharp", {})
        assert "Item B" in result

    def test_real_csharp_rules_net8_includes_net8_section(self):
        rules_file = _commands / "lang-rules" / "csharp.md"
        if not rules_file.is_file():
            pytest.skip("csharp.md not found")
        rules = rules_file.read_text(encoding="utf-8")
        result = self._filter(rules, "csharp", {"dotnet": "8.0"})
        assert ".NET 8+" in result
        assert "All Versions" in result

    def test_real_csharp_rules_net6_excludes_net8_section(self):
        rules_file = _commands / "lang-rules" / "csharp.md"
        if not rules_file.is_file():
            pytest.skip("csharp.md not found")
        rules = rules_file.read_text(encoding="utf-8")
        result = self._filter(rules, "csharp", {"dotnet": "6.0"})
        assert ".NET 8+" not in result


# ---------------------------------------------------------------------------
# Registry-mode file filter tests (VERIFY requirement)
# ---------------------------------------------------------------------------

class TestFileFilterRegistryVerify:

    def _yml(self) -> Path:
        return _commands / "languages.yml"

    def test_cs_files_reviewed_csharp_registered(self):
        files = [FakeFC("src/Service.cs"), FakeFC("config.json")]
        yml = self._yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        assert any(f.path == "src/Service.cs" for f in code)
        assert any(f.path == "config.json" for f in skipped)

    def test_json_files_skipped_not_registered(self):
        files = [FakeFC("package.json"), FakeFC("src/main.py")]
        yml = self._yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        skip_paths = {f.path for f in skipped}
        assert "package.json" in skip_paths

    def test_codereview_md_skipped(self):
        files = [FakeFC(".codereview.md"), FakeFC("src/app.py")]
        yml = self._yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        assert not any(f.path == ".codereview.md" for f in code)
