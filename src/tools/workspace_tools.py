"""
Workspace tools — local file reads, code search, and git blame.

These tools operate on the cloned workspace directory and don't need
VCS API credentials.
"""

import json
import subprocess
from pathlib import Path

from tools.registry import Tool, ToolRegistry


def _resolve_workspace_path(workspace: Path, file_path: str) -> Path:
    """Resolve a file path safely within the workspace.

    ADO file paths start with '/' (e.g. '/BluSKYFunctionApps/...').
    On Windows, Path(workspace) / '/absolute' drops the workspace prefix.
    Strip the leading '/' so it joins correctly.
    """
    cleaned = file_path.lstrip("/")
    resolved = (workspace / cleaned).resolve()
    ws_resolved = workspace.resolve()
    if not str(resolved).startswith(str(ws_resolved)):
        raise ValueError(f"Path outside workspace: {file_path}")
    return resolved


def _find_file_in_workspace(workspace: Path, file_path: str) -> "Path | None":
    """Search for a file by matching its suffix against workspace contents.

    ADO API returns paths without the repo-name prefix (e.g. 'DatalakeAPIs/Foo.cs')
    but the cloned workspace has them under 'RepoName/DatalakeAPIs/Foo.cs'.

    Three-stage fallback: exact path → suffix match → filename-only match.
    """
    cleaned = file_path.lstrip("/").replace("\\", "/")
    suffix = "/" + cleaned
    filename = cleaned.rsplit("/", 1)[-1].lower()

    try:
        proc = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True,
            encoding="utf-8", timeout=10, cwd=str(workspace),
        )
        if proc.returncode == 0:
            lines = proc.stdout.splitlines()

            for line in lines:
                normalized = line.replace("\\", "/")
                if normalized == cleaned or normalized.endswith(suffix):
                    candidate = (workspace / line).resolve()
                    if candidate.is_file():
                        return candidate

            # Filename-only fallback: match by basename (single match only to avoid ambiguity)
            basename_matches = []
            for line in lines:
                normalized = line.replace("\\", "/")
                if normalized.rsplit("/", 1)[-1].lower() == filename:
                    candidate = (workspace / line).resolve()
                    if candidate.is_file():
                        basename_matches.append(candidate)
            if len(basename_matches) == 1:
                return basename_matches[0]
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def register_workspace_tools(registry: ToolRegistry, workspace: Path):
    # -- search_code ----------------------------------------------------------

    def handle_search_code(args: dict) -> str:
        pattern = args["pattern"]
        max_results = args.get("max_results", 50)

        cmd = ["rg", "--no-heading", "--line-number", "--max-count", str(max_results), pattern]

        file_type = args.get("file_type")
        if file_type:
            cmd.extend(["--type", file_type])

        search_path = str(workspace)
        paths = args.get("paths")
        if paths:
            try:
                candidate = _resolve_workspace_path(workspace, paths)
                if candidate.exists():
                    search_path = str(candidate)
            except ValueError:
                pass

        cmd.append(search_path)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=30, cwd=str(workspace),
            )
            output = proc.stdout or ""
            if len(output) > 15000:
                output = output[:15000] + "\n... [truncated]"
            if not output and proc.returncode == 1:
                return json.dumps({"matches": [], "message": "No matches found"})
            return json.dumps({"matches": output, "pattern": pattern})
        except FileNotFoundError:
            return _fallback_grep(pattern, search_path, max_results, workspace)
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Search timed out after 30s"})

    registry.register(Tool(
        name="search_code",
        schema={
            "description": (
                "Search for a pattern across files in the workspace using ripgrep. "
                "Use this to find callers, usages, or references."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "file_type": {
                        "type": "string",
                        "description": "File type filter (e.g., 'cs', 'py', 'js'). Optional.",
                    },
                    "paths": {
                        "type": "string",
                        "description": "Subdirectory to search within (relative to workspace root). Optional.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return. Default 50.",
                    },
                },
                "required": ["pattern"],
            },
        },
        handler=handle_search_code,
    ))

    # -- read_local_file ------------------------------------------------------

    def handle_read_local_file(args: dict) -> str:
        file_path = args["file_path"]
        max_lines = args.get("max_lines", 500)

        try:
            resolved = _resolve_workspace_path(workspace, file_path)
        except ValueError:
            return json.dumps({"error": "Path traversal not allowed"})

        if not resolved.exists():
            resolved = _find_file_in_workspace(workspace, file_path)
            if resolved is None:
                return json.dumps({"error": f"File not found: {file_path}"})

        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > max_lines:
                content = "\n".join(lines[:max_lines])
                return json.dumps({
                    "file": file_path,
                    "content": content,
                    "truncated": True,
                    "total_lines": len(lines),
                    "returned_lines": max_lines,
                })
            return json.dumps({"file": file_path, "content": "\n".join(lines)})
        except Exception as e:
            return json.dumps({"error": f"Could not read file: {e}"})

    registry.register(Tool(
        name="read_local_file",
        schema={
            "description": (
                "Read a file from the cloned workspace on disk. "
                "Use this for files already checked out locally (e.g., config files, test files)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path relative to workspace root",
                    },
                    "max_lines": {
                        "type": "integer",
                        "description": "Maximum number of lines to return. Default 500.",
                    },
                },
                "required": ["file_path"],
            },
        },
        handler=handle_read_local_file,
    ))

    # -- git_blame ------------------------------------------------------------

    def handle_git_blame(args: dict) -> str:
        file_path = args["file_path"]

        try:
            resolved = _resolve_workspace_path(workspace, file_path)
        except ValueError:
            return json.dumps({"error": "Path traversal not allowed"})

        if not resolved.exists():
            resolved = _find_file_in_workspace(workspace, file_path)
            if resolved is None:
                return json.dumps({"error": f"File not found in workspace: {file_path}"})

        relative_path = str(resolved.relative_to(workspace.resolve())).replace("\\", "/")

        cmd = ["git", "blame", "--porcelain"]

        start = args.get("start_line")
        end = args.get("end_line")
        if start and end:
            cmd.extend(["-L", f"{start},{end}"])
        elif start:
            cmd.extend(["-L", f"{start},+20"])

        cmd.append(relative_path)

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                timeout=30, cwd=str(workspace),
            )
            if proc.returncode != 0:
                return json.dumps({"error": f"git blame failed: {proc.stderr.strip()}"})

            output = proc.stdout or ""
            if len(output) > 10000:
                output = output[:10000] + "\n... [truncated]"
            return json.dumps({"file": file_path, "blame": output})
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "git blame timed out"})

    registry.register(Tool(
        name="git_blame",
        schema={
            "description": (
                "Run git blame on a file in the workspace to see who last modified each line. "
                "Optionally limit to a line range."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "File path relative to workspace root",
                    },
                    "start_line": {"type": "integer", "description": "Start line number (optional)"},
                    "end_line": {"type": "integer", "description": "End line number (optional)"},
                },
                "required": ["file_path"],
            },
        },
        handler=handle_git_blame,
    ))


def _fallback_grep(pattern: str, search_path: str, max_results: int, workspace: Path) -> str:
    cmd = ["git", "grep", "-n", "-I", f"--max-count={max_results}", pattern, "--", "."]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, encoding="utf-8",
            timeout=30, cwd=search_path,
        )
        output = proc.stdout or ""
        if len(output) > 15000:
            output = output[:15000] + "\n... [truncated]"
        return json.dumps({"matches": output, "pattern": pattern, "backend": "git-grep"})
    except Exception as e:
        return json.dumps({"error": f"Search unavailable: {e}"})
