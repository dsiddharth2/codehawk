"""
Stack detector — reads project config files to extract framework versions.

Detects languages and framework versions from the workspace root config files.
Monorepo limitation: only root-level configs are parsed (per-directory profiles
deferred to a future sprint).
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("codehawk.stack_detector")


@dataclass
class StackProfile:
    languages: list[str] = field(default_factory=list)
    frameworks: dict[str, dict[str, str]] = field(default_factory=dict)
    detected_from: list[str] = field(default_factory=list)


def detect(workspace: str | Path) -> StackProfile:
    """Detect the technology stack by reading config files at the workspace root."""
    root = Path(workspace)
    profile = StackProfile()

    _detect_csharp(root, profile)
    _detect_node(root, profile)
    _detect_python(root, profile)
    _detect_java(root, profile)
    _detect_go(root, profile)
    _detect_rust(root, profile)
    _detect_cpp(root, profile)
    _detect_swift(root, profile)
    _detect_android(root, profile)
    _detect_ruby(root, profile)
    _detect_php(root, profile)

    logger.info(
        "Stack detection complete: languages=%s detected_from=%s",
        profile.languages,
        profile.detected_from,
    )
    return profile


# ---------------------------------------------------------------------------
# Language detectors
# ---------------------------------------------------------------------------

def _detect_csharp(root: Path, profile: StackProfile) -> None:
    csproj_files = list(root.glob("*.csproj"))
    if not csproj_files:
        return

    versions: dict[str, str] = {}
    for csproj in csproj_files:
        try:
            tree = ET.parse(csproj)
            xml_root = tree.getroot()

            tf = xml_root.findtext(".//TargetFramework") or xml_root.findtext(".//TargetFrameworks", "")
            if tf:
                # Extract major.minor from net8.0 / netcoreapp3.1 / net48 etc.
                match = re.search(r"net(\d+\.\d+|\d+)", tf.split(";")[0])
                if match:
                    versions["dotnet"] = match.group(1)

            for pkg in xml_root.findall(".//PackageReference"):
                name = pkg.get("Include", "")
                ver = pkg.get("Version", "")
                if not ver:
                    continue
                name_lower = name.lower()
                if "entityframeworkcore" in name_lower and "efcore" not in versions:
                    versions["efcore"] = ver
                elif "aspnetcore" in name_lower or name_lower == "microsoft.aspnetcore.app":
                    versions["aspnetcore"] = ver

            profile.detected_from.append(csproj.name)
        except Exception as exc:
            logger.debug("Failed to parse %s: %s", csproj, exc)

    if versions:
        _add_language(profile, "csharp", versions)


def _detect_node(root: Path, profile: StackProfile) -> None:
    pkg_file = root / "package.json"
    if not pkg_file.is_file():
        return

    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Failed to parse package.json: %s", exc)
        return

    profile.detected_from.append("package.json")
    all_deps: dict[str, str] = {}
    all_deps.update(pkg.get("dependencies", {}))
    all_deps.update(pkg.get("devDependencies", {}))

    js_versions: dict[str, str] = {}

    # Node engine constraint
    engines = pkg.get("engines", {})
    if "node" in engines:
        js_versions["node"] = engines["node"]

    # React
    react_ver = all_deps.get("react", "")
    if react_ver:
        js_versions["react"] = _clean_semver(react_ver)
        _add_language(profile, "react", {})

    # Angular
    ng_ver = all_deps.get("@angular/core", "")
    if ng_ver:
        js_versions["angular"] = _clean_semver(ng_ver)

    # Vue
    vue_ver = all_deps.get("vue", "")
    if vue_ver:
        js_versions["vue"] = _clean_semver(vue_ver)

    # TypeScript
    ts_ver = all_deps.get("typescript", "")
    if ts_ver:
        js_versions["typescript"] = _clean_semver(ts_ver)

    # Next.js
    next_ver = all_deps.get("next", "")
    if next_ver:
        js_versions["next"] = _clean_semver(next_ver)

    # Determine base language (TypeScript vs JavaScript)
    tsconfig = root / "tsconfig.json"
    if tsconfig.is_file() or "typescript" in all_deps or any(f.suffix == ".ts" for f in root.glob("*.ts")):
        _add_language(profile, "typescript", js_versions)
        if tsconfig.is_file():
            profile.detected_from.append("tsconfig.json")
            _parse_tsconfig(tsconfig, js_versions)
    else:
        _add_language(profile, "javascript", js_versions)


def _parse_tsconfig(tsconfig: Path, versions: dict[str, str]) -> None:
    try:
        import json as _json
        text = tsconfig.read_text(encoding="utf-8")
        # Strip JSON comments (tsconfig allows them)
        text = re.sub(r"//.*$", "", text, flags=re.MULTILINE)
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
        cfg = _json.loads(text)
        compiler = cfg.get("compilerOptions", {})
        if "target" in compiler:
            versions["ts_target"] = compiler["target"]
        if compiler.get("strict"):
            versions["ts_strict"] = "true"
    except Exception as exc:
        logger.debug("Failed to parse tsconfig.json: %s", exc)


def _detect_python(root: Path, profile: StackProfile) -> None:
    versions: dict[str, str] = {}
    detected = False

    req_file = root / "requirements.txt"
    if req_file.is_file():
        detected = True
        profile.detected_from.append("requirements.txt")
        _parse_requirements_txt(req_file, versions)

    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        detected = True
        profile.detected_from.append("pyproject.toml")
        _parse_pyproject_toml(pyproject, versions)

    pipfile = root / "Pipfile"
    if pipfile.is_file():
        detected = True
        profile.detected_from.append("Pipfile")
        _parse_pipfile(pipfile, versions)

    setup_py = root / "setup.py"
    if setup_py.is_file() and not detected:
        detected = True
        profile.detected_from.append("setup.py")

    if detected:
        _add_language(profile, "python", versions)


def _parse_requirements_txt(path: Path, versions: dict[str, str]) -> None:
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for pkg, key in [("django", "django"), ("flask", "flask"), ("fastapi", "fastapi")]:
                if line.lower().startswith(pkg):
                    ver_match = re.search(r"[=><~!]+\s*(\d+[\d.]*)", line)
                    if ver_match:
                        versions[key] = ver_match.group(1)
    except Exception as exc:
        logger.debug("Failed to parse requirements.txt: %s", exc)


def _parse_pyproject_toml(path: Path, versions: dict[str, str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
        # Match python_requires = ">=3.10" (setuptools style)
        py_ver = re.search(r'python_requires\s*=\s*["\']([^"\']+)["\']', text)
        if not py_ver:
            # Match python = ">=3.10" (poetry style in [tool.poetry.dependencies])
            py_ver = re.search(r'^\s*python\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if py_ver:
            versions["python"] = py_ver.group(1)
        for pkg in ("django", "flask", "fastapi"):
            m = re.search(rf'["\']?{pkg}["\']?\s*[=>=~^]+\s*["\']?([^\s"\']+)["\']?', text, re.IGNORECASE)
            if m:
                versions[pkg] = m.group(1)
    except Exception as exc:
        logger.debug("Failed to parse pyproject.toml: %s", exc)


def _parse_pipfile(path: Path, versions: dict[str, str]) -> None:
    try:
        text = path.read_text(encoding="utf-8")
        py_ver = re.search(r'python_version\s*=\s*["\']([^"\']+)["\']', text)
        if py_ver:
            versions["python"] = py_ver.group(1)
    except Exception as exc:
        logger.debug("Failed to parse Pipfile: %s", exc)


def _detect_java(root: Path, profile: StackProfile) -> None:
    versions: dict[str, str] = {}
    detected = False

    pom = root / "pom.xml"
    if pom.is_file():
        detected = True
        profile.detected_from.append("pom.xml")
        try:
            tree = ET.parse(pom)
            xml_root = tree.getroot()
            ns = {"m": "http://maven.apache.org/POM/4.0.0"}
            # Try with and without namespace
            java_ver = (
                xml_root.findtext(".//m:maven.compiler.source", namespaces=ns)
                or xml_root.findtext(".//maven.compiler.source")
                or xml_root.findtext(".//m:java.version", namespaces=ns)
                or xml_root.findtext(".//java.version")
            )
            if java_ver:
                versions["java"] = java_ver
            sb_ver = (
                xml_root.findtext(".//m:parent/m:version", namespaces=ns)
                or xml_root.findtext(".//parent/version")
            )
            parent_artifact = (
                xml_root.findtext(".//m:parent/m:artifactId", namespaces=ns)
                or xml_root.findtext(".//parent/artifactId", "")
            )
            if sb_ver and "spring-boot" in (parent_artifact or ""):
                versions["spring_boot"] = sb_ver
        except Exception as exc:
            logger.debug("Failed to parse pom.xml: %s", exc)

    for gradle_name in ("build.gradle", "build.gradle.kts"):
        gradle = root / gradle_name
        if gradle.is_file():
            detected = True
            profile.detected_from.append(gradle_name)
            try:
                text = gradle.read_text(encoding="utf-8")
                java_ver = re.search(r"sourceCompatibility\s*=\s*['\"]?(\d+)['\"]?", text)
                if java_ver:
                    versions["java"] = java_ver.group(1)
                sb_ver = re.search(r'id\s*\(["\']org\.springframework\.boot["\']\)\s+version\s+["\']([^"\']+)["\']', text)
                if sb_ver:
                    versions["spring_boot"] = sb_ver.group(1)
                if "org.jetbrains.kotlin" in text or "kotlin(" in text:
                    _add_language(profile, "kotlin", versions)
                if "compose" in text.lower():
                    versions["compose"] = "detected"
            except Exception as exc:
                logger.debug("Failed to parse %s: %s", gradle_name, exc)

    if detected:
        _add_language(profile, "java", versions)


def _detect_go(root: Path, profile: StackProfile) -> None:
    gomod = root / "go.mod"
    if not gomod.is_file():
        return

    versions: dict[str, str] = {}
    profile.detected_from.append("go.mod")
    try:
        text = gomod.read_text(encoding="utf-8")
        go_ver = re.search(r"^go\s+(\d+\.\d+)", text, re.MULTILINE)
        if go_ver:
            versions["go"] = go_ver.group(1)
    except Exception as exc:
        logger.debug("Failed to parse go.mod: %s", exc)

    _add_language(profile, "go", versions)


def _detect_rust(root: Path, profile: StackProfile) -> None:
    cargo = root / "Cargo.toml"
    if not cargo.is_file():
        return

    versions: dict[str, str] = {}
    profile.detected_from.append("Cargo.toml")
    try:
        text = cargo.read_text(encoding="utf-8")
        edition = re.search(r'^edition\s*=\s*["\'](\d+)["\']', text, re.MULTILINE)
        if edition:
            versions["edition"] = edition.group(1)
        rust_ver = re.search(r'^rust-version\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
        if rust_ver:
            versions["rust"] = rust_ver.group(1)
    except Exception as exc:
        logger.debug("Failed to parse Cargo.toml: %s", exc)

    _add_language(profile, "rust", versions)


def _detect_cpp(root: Path, profile: StackProfile) -> None:
    cmake = root / "CMakeLists.txt"
    if not cmake.is_file():
        return

    versions: dict[str, str] = {}
    profile.detected_from.append("CMakeLists.txt")
    try:
        text = cmake.read_text(encoding="utf-8")
        std = re.search(r"CMAKE_CXX_STANDARD\s+(\d+)", text)
        if std:
            versions["cxx_standard"] = std.group(1)
        cmake_ver = re.search(r"cmake_minimum_required\s*\(\s*VERSION\s+(\S+)", text, re.IGNORECASE)
        if cmake_ver:
            versions["cmake"] = cmake_ver.group(1)
    except Exception as exc:
        logger.debug("Failed to parse CMakeLists.txt: %s", exc)

    _add_language(profile, "cpp", versions)


def _detect_swift(root: Path, profile: StackProfile) -> None:
    package_swift = root / "Package.swift"
    podfile = root / "Podfile"

    if not package_swift.is_file() and not podfile.is_file():
        return

    versions: dict[str, str] = {}

    if package_swift.is_file():
        profile.detected_from.append("Package.swift")
        try:
            text = package_swift.read_text(encoding="utf-8")
            swift_ver = re.search(r"swift-tools-version:(\d+\.\d+)", text)
            if swift_ver:
                versions["swift_tools"] = swift_ver.group(1)
            ios_target = re.search(r'\.iOS\s*\(\s*\.v(\d+)\s*\)', text)
            if ios_target:
                versions["ios_target"] = ios_target.group(1)
        except Exception as exc:
            logger.debug("Failed to parse Package.swift: %s", exc)

    if podfile.is_file():
        profile.detected_from.append("Podfile")
        try:
            text = podfile.read_text(encoding="utf-8")
            ios_ver = re.search(r"platform\s+:ios,\s*['\"]?(\d+\.\d+)['\"]?", text)
            if ios_ver:
                versions.setdefault("ios_target", ios_ver.group(1))
        except Exception as exc:
            logger.debug("Failed to parse Podfile: %s", exc)

    _add_language(profile, "swift", versions)


def _detect_android(root: Path, profile: StackProfile) -> None:
    manifest = root / "AndroidManifest.xml"
    if not manifest.is_file():
        # Also check app/src/main
        manifest = root / "app" / "src" / "main" / "AndroidManifest.xml"
        if not manifest.is_file():
            return

    versions: dict[str, str] = {}
    profile.detected_from.append("AndroidManifest.xml")
    try:
        tree = ET.parse(manifest)
        xml_root = tree.getroot()
        uses_sdk = xml_root.find("uses-sdk")
        if uses_sdk is not None:
            ns = "http://schemas.android.com/apk/res/android"
            min_sdk = uses_sdk.get(f"{{{ns}}}minSdkVersion", "")
            target_sdk = uses_sdk.get(f"{{{ns}}}targetSdkVersion", "")
            if min_sdk:
                versions["min_sdk"] = min_sdk
            if target_sdk:
                versions["target_sdk"] = target_sdk
    except Exception as exc:
        logger.debug("Failed to parse AndroidManifest.xml: %s", exc)

    _add_language(profile, "android", versions)


def _detect_ruby(root: Path, profile: StackProfile) -> None:
    gemfile = root / "Gemfile"
    if not gemfile.is_file():
        return

    versions: dict[str, str] = {}
    profile.detected_from.append("Gemfile")
    try:
        text = gemfile.read_text(encoding="utf-8")
        ruby_ver = re.search(r"ruby\s+['\"]([^'\"]+)['\"]", text)
        if ruby_ver:
            versions["ruby"] = ruby_ver.group(1)
        rails_ver = re.search(r"gem\s+['\"]rails['\"],\s*['\"]?([~>=<\s\d.]+)['\"]?", text)
        if rails_ver:
            versions["rails"] = rails_ver.group(1).strip()
    except Exception as exc:
        logger.debug("Failed to parse Gemfile: %s", exc)

    _add_language(profile, "ruby", versions)


def _detect_php(root: Path, profile: StackProfile) -> None:
    composer = root / "composer.json"
    if not composer.is_file():
        return

    versions: dict[str, str] = {}
    profile.detected_from.append("composer.json")
    try:
        data: dict[str, Any] = json.loads(composer.read_text(encoding="utf-8"))
        require = data.get("require", {})
        php_ver = require.get("php", "")
        if php_ver:
            versions["php"] = php_ver
        for pkg, key in [
            ("laravel/framework", "laravel"),
            ("symfony/symfony", "symfony"),
            ("symfony/framework-bundle", "symfony"),
        ]:
            if pkg in require:
                versions[key] = require[pkg]
    except Exception as exc:
        logger.debug("Failed to parse composer.json: %s", exc)

    _add_language(profile, "php", versions)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _add_language(profile: StackProfile, lang: str, versions: dict[str, str]) -> None:
    if lang not in profile.languages:
        profile.languages.append(lang)
    profile.frameworks.setdefault(lang, {}).update(versions)


def _clean_semver(version: str) -> str:
    """Strip leading semver range characters to get a plain version string."""
    return re.sub(r"^[\^~>=<!\s]+", "", version).strip()
