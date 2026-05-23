"""
Unit tests for src/file_filter.py

Covers:
  - parse_skip_extensions: standard CSV, leading-dot normalization, mixed case,
    whitespace, empty string, duplicates
  - filter_changed_files (blacklist mode): keeps code files, skips non-code by extension,
    skips deleted files regardless of extension, empty list, all-skipped scenario
  - filter_changed_files (registry mode): uses languages.yml extension registry,
    skips unregistered extensions, falls back to blacklist when registry unavailable
"""

import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List

import pytest

_src = Path(__file__).parent.parent.parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from file_filter import parse_skip_extensions, filter_changed_files

# Sentinel path that forces blacklist fallback (registry file does not exist)
_NO_REGISTRY = Path("/nonexistent/languages.yml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeFileChange:
    path: str
    change_type: str = "edit"
    additions: int = 0
    deletions: int = 0


# ---------------------------------------------------------------------------
# Tests for parse_skip_extensions
# ---------------------------------------------------------------------------

class TestParseSkipExtensions:
    def test_standard_csv(self):
        result = parse_skip_extensions(".md,.json,.yaml")
        assert result == {".md", ".json", ".yaml"}

    def test_adds_leading_dot(self):
        result = parse_skip_extensions("md,json,yaml")
        assert result == {".md", ".json", ".yaml"}

    def test_mixed_case_normalized(self):
        result = parse_skip_extensions(".MD,.JSON,.YAML")
        assert result == {".md", ".json", ".yaml"}

    def test_whitespace_stripped(self):
        result = parse_skip_extensions("  .md ,  .json , .yaml  ")
        assert result == {".md", ".json", ".yaml"}

    def test_empty_string_returns_empty_set(self):
        assert parse_skip_extensions("") == set()
        assert parse_skip_extensions("   ") == set()

    def test_mixed_dot_prefix_and_no_prefix(self):
        result = parse_skip_extensions(".md,json,.YAML,lock")
        assert result == {".md", ".json", ".yaml", ".lock"}

    def test_duplicates_deduplicated(self):
        result = parse_skip_extensions(".md,.md,.json")
        assert result == {".md", ".json"}

    def test_large_default_csv(self):
        csv = ".md,.json,.yaml,.yml,.xml,.lock,.png,.jpg,.jpeg,.gif,.svg,.ico"
        result = parse_skip_extensions(csv)
        assert ".md" in result
        assert ".png" in result
        assert len(result) == 12


# ---------------------------------------------------------------------------
# Tests for filter_changed_files
# ---------------------------------------------------------------------------

class TestFilterChangedFiles:
    """Blacklist-mode tests — pass _NO_REGISTRY to bypass the language registry."""

    def _skip(self):
        return parse_skip_extensions(".md,.json,.yaml,.lock,.png")

    def test_keeps_python_files(self):
        files = [FakeFileChange("src/main.py"), FakeFileChange("src/utils.py")]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert len(code) == 2
        assert len(skipped) == 0

    def test_skips_markdown_files(self):
        files = [FakeFileChange("README.md"), FakeFileChange("CHANGELOG.md")]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert len(code) == 0
        assert len(skipped) == 2

    def test_skips_deleted_files_regardless_of_extension(self):
        files = [
            FakeFileChange("src/main.py", change_type="delete"),
            FakeFileChange("src/utils.py", change_type="edit"),
        ]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert len(code) == 1
        assert code[0].path == "src/utils.py"
        assert len(skipped) == 1

    def test_mixed_extensions(self):
        files = [
            FakeFileChange("src/app.py"),
            FakeFileChange("README.md"),
            FakeFileChange("config.json"),
            FakeFileChange("src/component.ts"),
            FakeFileChange("styles.css"),
            FakeFileChange("data.yaml"),
            FakeFileChange("image.png"),
        ]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        code_paths = {f.path for f in code}
        skip_paths = {f.path for f in skipped}
        assert code_paths == {"src/app.py", "src/component.ts", "styles.css"}
        assert skip_paths == {"README.md", "config.json", "data.yaml", "image.png"}

    def test_empty_list(self):
        code, skipped = filter_changed_files([], self._skip(), languages_yml=_NO_REGISTRY)
        assert code == []
        assert skipped == []

    def test_all_skipped_scenario(self):
        files = [
            FakeFileChange("README.md"),
            FakeFileChange("package.json"),
            FakeFileChange("schema.yaml"),
        ]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert code == []
        assert len(skipped) == 3

    def test_deleted_non_code_still_in_skipped(self):
        files = [FakeFileChange("README.md", change_type="delete")]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert len(code) == 0
        assert len(skipped) == 1

    def test_no_skip_extensions(self):
        files = [FakeFileChange("README.md"), FakeFileChange("src/app.py")]
        code, skipped = filter_changed_files(files, set(), languages_yml=_NO_REGISTRY)
        # Blacklist mode with empty skip set: only deleted files are skipped
        assert len(code) == 2
        assert len(skipped) == 0

    def test_dict_style_file_objects(self):
        files = [
            {"path": "src/main.py", "change_type": "edit"},
            {"path": "README.md", "change_type": "edit"},
        ]
        code, skipped = filter_changed_files(files, self._skip(), languages_yml=_NO_REGISTRY)
        assert len(code) == 1
        assert len(skipped) == 1


class TestFilterChangedFilesRegistry:
    """Registry-mode tests — use a real or synthetic languages.yml."""

    def _real_languages_yml(self) -> Path:
        """Return the path to the actual languages.yml in this repo."""
        return Path(__file__).parent.parent.parent / "commands" / "languages.yml"

    def test_cs_files_kept_when_csharp_registered(self):
        files = [FakeFileChange("src/Service.cs"), FakeFileChange("config.json")]
        yml = self._real_languages_yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        code_paths = {f.path for f in code}
        assert "src/Service.cs" in code_paths
        assert "config.json" not in code_paths

    def test_json_files_skipped_when_not_registered(self):
        files = [FakeFileChange("package.json"), FakeFileChange("src/app.py")]
        yml = self._real_languages_yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        code_paths = {f.path for f in code}
        skip_paths = {f.path for f in skipped}
        assert "src/app.py" in code_paths
        assert "package.json" in skip_paths

    def test_deleted_always_skipped_in_registry_mode(self):
        files = [FakeFileChange("src/app.cs", change_type="delete")]
        yml = self._real_languages_yml()
        if not yml.is_file():
            pytest.skip("languages.yml not found")
        code, skipped = filter_changed_files(files, set(), languages_yml=yml)
        assert len(code) == 0
        assert len(skipped) == 1

    def test_missing_registry_falls_back_to_blacklist(self):
        files = [FakeFileChange("README.md"), FakeFileChange("src/app.py")]
        skip = parse_skip_extensions(".md")
        code, skipped = filter_changed_files(files, skip, languages_yml=_NO_REGISTRY)
        assert len(code) == 1
        assert code[0].path == "src/app.py"

    def test_synthetic_registry(self):
        yml_content = "languages:\n  csharp:\n    extensions: [.cs]\n    config_files: []\n    rules_file: commands/lang-rules/csharp.md\n"
        with tempfile.NamedTemporaryFile(suffix=".yml", mode="w", delete=False) as f:
            f.write(yml_content)
            tmp_path = Path(f.name)
        try:
            files = [FakeFileChange("src/Controller.cs"), FakeFileChange("README.md")]
            code, skipped = filter_changed_files(files, set(), languages_yml=tmp_path)
            assert len(code) == 1
            assert code[0].path == "src/Controller.cs"
            assert len(skipped) == 1
        finally:
            tmp_path.unlink(missing_ok=True)
