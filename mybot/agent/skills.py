"""Skills loader — discovers SKILL.md files from the workspace skills/ directory.

Each skill is a folder containing a SKILL.md file with optional YAML frontmatter:

    ---
    name: weather-expert
    description: 专业的天气分析师
    always: false
    triggers:
      - 天气
      - 气温
    ---
    # Skill instructions injected into system prompt ...

Metadata fields:
  - name:         skill identifier (defaults to folder name)
  - description:  one-line summary shown in skill list
  - always:       if true, always inject into system prompt (default false)
  - triggers:     list of keywords; if any appears in user message, activate the skill
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class Skill:
    """A single loaded skill."""

    name: str
    description: str
    always: bool = False
    triggers: list[str] = field(default_factory=list)
    content: str = ""  # markdown body injected into system prompt
    path: Path | None = None


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_skill_file(path: Path) -> Skill | None:
    """Parse a SKILL.md file into a Skill object."""
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        logger.warning("SkillsLoader: cannot read {}", path)
        return None

    meta: dict[str, Any] = {}
    body = text

    m = _FRONTMATTER_RE.match(text)
    if m:
        raw_yaml = m.group(1)
        body = text[m.end():]
        # Simple YAML parser — avoids requiring pyyaml
        for line in raw_yaml.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key == "triggers":
                    # triggers are on subsequent lines starting with "- "
                    continue
                if val.lower() in ("true", "yes", "1"):
                    meta[key] = True
                elif val.lower() in ("false", "no", "0"):
                    meta[key] = False
                else:
                    meta[key] = val.strip("\"'")

        # Parse triggers list
        triggers: list[str] = []
        in_triggers = False
        for line in raw_yaml.splitlines():
            stripped = line.strip()
            if stripped.startswith("triggers:"):
                in_triggers = True
                # inline value: triggers: [天气, 气温]
                inline = stripped[len("triggers:"):].strip()
                if inline.startswith("["):
                    items = inline.strip("[]").split(",")
                    triggers = [i.strip().strip("\"'") for i in items if i.strip()]
                    in_triggers = False
                continue
            if in_triggers:
                if stripped.startswith("- "):
                    triggers.append(stripped[2:].strip().strip("\"'"))
                elif stripped and not stripped.startswith("#"):
                    in_triggers = False
        if triggers:
            meta["triggers"] = triggers

    folder_name = path.parent.name
    name = meta.get("name", folder_name)
    description = meta.get("description", "")
    always = meta.get("always", False)
    triggers_list = meta.get("triggers", [])

    body = body.strip()
    if not body:
        logger.debug("SkillsLoader: {} has empty body, skipping", path)
        return None

    return Skill(
        name=str(name),
        description=str(description),
        always=bool(always),
        triggers=list(triggers_list),
        content=body,
        path=path,
    )


class SkillsLoader:
    """Discover and load skills from the workspace skills/ directory."""

    def __init__(
        self,
        workspace: Path | str | None = None,
        *,
        disabled_skills: set[str] | None = None,
    ):
        self.workspace = Path(workspace) if workspace else Path.cwd()
        self.disabled_skills = disabled_skills or set()
        self._skills: dict[str, Skill] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def load(self) -> dict[str, Skill]:
        """Scan skills/ directory and return all valid skills."""
        skills_dir = self.workspace / "skills"
        if not skills_dir.is_dir():
            logger.info("SkillsLoader: no skills/ directory at {}", skills_dir)
            self._loaded = True
            return {}

        found: dict[str, Skill] = {}
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.is_file():
                continue
            skill = _parse_skill_file(skill_file)
            if skill is None:
                continue
            if skill.name in self.disabled_skills:
                logger.debug("SkillsLoader: skipping disabled skill '{}'", skill.name)
                continue
            found[skill.name] = skill
            logger.info(
                "SkillsLoader: loaded skill '{}' (always={}, triggers={})",
                skill.name,
                skill.always,
                skill.triggers,
            )

        self._skills = found
        self._loaded = True
        logger.info("SkillsLoader: {} skills loaded", len(found))
        return found

    def _ensure_loaded(self) -> dict[str, Skill]:
        if not self._loaded:
            self.load()
        return self._skills

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def skills(self) -> dict[str, Skill]:
        return self._ensure_loaded()

    def get_always_skills(self) -> list[Skill]:
        """Return skills that should be injected into every prompt."""
        return [s for s in self._ensure_loaded().values() if s.always]

    def match_skills(self, user_message: str) -> list[Skill]:
        """Return skills whose triggers match the user message."""
        if not user_message:
            return []
        msg_lower = user_message.lower()
        matched: list[Skill] = []
        for skill in self._ensure_loaded().values():
            if skill.always:
                continue  # always skills are handled separately
            for trigger in skill.triggers:
                if trigger.lower() in msg_lower:
                    matched.append(skill)
                    break
        return matched

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """Build a short summary of available skills for the system prompt."""
        exclude = exclude or set()
        lines: list[str] = []
        for skill in self._skills.values():
            if skill.name in exclude:
                continue
            desc = skill.description or "(no description)"
            lines.append(f"- **{skill.name}**: {desc}")
        return "\n".join(lines)

    def load_skills_for_content(self, skills: list[Skill]) -> str:
        """Concatenate skill contents for injection into system prompt."""
        parts: list[str] = []
        for skill in skills:
            if skill.content:
                parts.append(f"## Skill: {skill.name}\n\n{skill.content}")
        return "\n\n".join(parts)
