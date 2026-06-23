from pathlib import Path
import re



from mybot.agent.tools.base import Tool, tool_parameters
from mybot.agent.tools.schema import (
    tool_parameters_schema,
    StringSchema,
    IntegerSchema,
    BooleanSchema,
)
from mybot.config_base import Base
from typing import Any


class FileToolsConfig(Base):

    enable: bool = True


class _FsTool(Tool):
    """Shared base for filesystem tools — common init and path resolution."""

    config_key = "file"

    @classmethod
    def config_cls(cls):
        return FileToolsConfig

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return ctx.config.file.enable

    def __init__(
        self,
        workspace: Path | None = None,
        allowed_dir: Path | None = None,
    ):
        self.workspace = (workspace,)
        self.allowed_dir = (allowed_dir,)

    @classmethod
    def create(cls, ctx: Any) -> Tool:

        restrict = ctx.restrict_to_workspace or ctx.config.exec.sandbox
        allowed_dir = Path(ctx.workspace) if restrict else None
        return cls(
            workspace=Path(ctx.workspace),
            allowed_dir=allowed_dir,
        )

    # def _resolve(self, path: str) -> Path:
    #     access = current_tool_workspace(
    #         self._workspace,
    #         restrict_to_workspace=self._restrict_to_workspace,
    #         sandbox_restricts_workspace=self._sandbox_restricts_workspace,
    #     )
    #     return resolve_workspace_path(
    #         path,
    #         access.project_path,
    #         access.allowed_root,
    #         self._extra_allowed_dirs,
    #     )

    # def _display_workspace(self) -> Path | None:
    #     return current_tool_workspace(self._workspace).project_path


# ---------------------------------------------------------------------------
# read_file
# ---------------------------------------------------------------------------


_BLOCKED_DEVICE_PATHS = frozenset(
    {
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/full",
        "/dev/stdin",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/tty",
        "/dev/console",
        "/dev/fd/0",
        "/dev/fd/1",
        "/dev/fd/2",
    }
)


def _is_blocked_device(path: str | Path) -> bool:
    """Check if path is a blocked device that could hang or produce infinite output."""
    import re

    raw = str(path)

    # Resolve symlinks to check the actual target
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw

    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/\d+/fd/[012]$", raw) or re.match(r"/proc/self/fd/[012]$", raw):
        return True
    if re.match(r"/proc/\d+/fd/[012]$", resolved) or re.match(
        r"/proc/self/fd/[012]$", resolved
    ):
        return True

    # Check if resolved path starts with /dev/ (covers symlinks to devices)
    if resolved.startswith("/dev/"):
        return True
    return False


@tool_parameters(
    tool_parameters_schema(
        path=StringSchema("The file path to read"),
        offset=IntegerSchema(
            1,
            description="Line number to start reading from (1-indexed, default 1)",
            minimum=1,
        ),
        limit=IntegerSchema(
            2000,
            description="Maximum number of lines to read (default 2000)",
            minimum=1,
        ),
        pages=StringSchema(
            "Page range for PDF files, e.g. '1-5' (default: all, max 20 pages)"
        ),
        force=BooleanSchema(
            description="Bypass same-file read deduplication and return content again.",
            default=False,
        ),
        required=["path"],
    )
)
class ReadFileTool(_FsTool):
    """Read file contents with optional line-based pagination."""

    _scopes = {"core", "subagent", "memory"}

    _MAX_CHARS = 128_000
    _DEFAULT_LIMIT = 2000
    _MAX_PDF_PAGES = 20

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return (
            "Read a file (text, image, or document). "
            "Text output format: LINE_NUM|CONTENT. "
            "Images return visual content for analysis. "
            "Supports PDF, DOCX, XLSX, PPTX documents. "
            "Use find_files/list_dir first when the path is uncertain. "
            "Read the relevant range before editing so replacements or patches "
            "are based on current content. "
            "Use offset and limit for large text files. "
            "Use force=true to re-read content even if unchanged. "
            "Reads exceeding ~128K chars are truncated."
        )

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        path: str | None = None,
        offset: int = 1,
        limit: int | None = None,
        pages: str | None = None,
        force: bool = False,
        **kwargs: Any,
    ) -> Any:
        try:
            if not path:
                return "Error reading file: Unknown path"

            fp = Path(path) if path else None
            if not fp.exists():
                return f"Error: File not found: {path}"
            if not fp.is_file():
                return f"Error: Not a file: {path}"

            if fp.suffix.lower() in {".docx", ".xlsx", ".pptx"}:
                return self._read_office_doc(fp)

            # Read text file with line-based pagination
            try:
                text = fp.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = fp.read_text(encoding="latin-1")

            lines = text.splitlines(keepends=True)
            total = len(lines)
            start = max(0, offset - 1)  # offset is 1-indexed
            end = start + (limit or self._DEFAULT_LIMIT)
            selected = lines[start:end]

            numbered = [
                f"{start + i + 1}|{line.rstrip(chr(10))}"
                for i, line in enumerate(selected)
            ]
            result = "\n".join(numbered)

            if len(result) > self._MAX_CHARS:
                result = result[: self._MAX_CHARS] + "\n... (truncated)"

            return result

        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {e}"

    def _read_office_doc(self, fp: Path) -> str:
        from mybot.utils.documents import extract_text

        result = extract_text(fp)

        if result is None:
            return f"Error: Unsupported file format: {fp.suffix}"

        if result.startswith("[error:"):
            return f"Error reading {fp.suffix.upper()} file: {result}"

        if not result:
            return f"({fp.suffix.upper().lstrip('.')} has no extractable text: {fp})"

        if len(result) > self._MAX_CHARS:
            result = (
                result[: self._MAX_CHARS]
                + "\n\n(Document text truncated at ~128K chars)"
            )

        return result
