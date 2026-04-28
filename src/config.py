"""
Configuration management using Pydantic settings.

Loads all configuration from environment variables with validation and type safety.
"""

from pathlib import Path
from typing import Dict, List, Literal, Optional
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


_current_file = Path(__file__).resolve()
_project_root = _current_file.parent.parent  # Go up from src/ to project root
_env_file_path = _project_root / ".env"


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.

    All settings are loaded from .env file or environment variables.
    """

    model_config = SettingsConfigDict(
        env_file=str(_env_file_path),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # VCS selection
    vcs: Literal["ado", "github"] = Field(
        default="ado",
        description="VCS provider: 'ado' for Azure DevOps, 'github' for GitHub"
    )

    # GitHub Configuration
    gh_token: Optional[str] = Field(
        default=None,
        description="GitHub personal access token (used when vcs=github)"
    )

    # Azure DevOps Configuration
    azure_devops_org: Optional[str] = Field(
        default=None,
        description="Azure DevOps organization name"
    )
    azure_devops_project: Optional[str] = Field(
        default=None,
        description="Azure DevOps project name"
    )
    azure_devops_pat: Optional[str] = Field(
        default=None,
        description="Azure DevOps Personal Access Token"
    )
    azure_devops_system_token: Optional[str] = Field(
        default=None,
        description="Azure DevOps System Access Token (provided in pipelines via $(System.AccessToken))"
    )
    azure_devops_repo: Optional[str] = Field(
        default=None,
        description="Azure DevOps repository name"
    )

    # Authentication Mode
    auth_mode: Literal["pat", "system_token", "auto"] = Field(
        default="auto",
        description="ADO authentication mode"
    )

    # Review Configuration
    min_confidence_score: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score to post findings"
    )
    max_comments_per_file: int = Field(
        default=5,
        ge=1,
        le=100,
        description="Maximum inline comments per file"
    )
    update_existing_summary: bool = Field(
        default=True,
        description="Update existing summary comment instead of creating a new one"
    )

    # Logging Configuration
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level"
    )
    log_file: str = Field(
        default="codehawk.log",
        description="Log file path"
    )
    log_format: Literal["json", "text"] = Field(
        default="json",
        description="Log format (json or text)"
    )
    enable_graph: bool = Field(
        default=True,
        description="Enable code-review-graph for AST-based blast-radius analysis"
    )

    # PR Scoring Configuration (Penalty-Based: Lower is Better)
    enable_pr_scoring: bool = Field(
        default=True,
        description="Enable PR penalty-based scoring system"
    )

    # Security Issue Penalties
    penalty_security_critical: float = Field(default=5.0, ge=0.0, le=100.0)
    penalty_security_warning: float = Field(default=4.0, ge=0.0, le=100.0)
    penalty_security_suggestion: float = Field(default=2.0, ge=0.0, le=100.0)

    # Performance Issue Penalties
    penalty_performance_critical: float = Field(default=3.0, ge=0.0, le=100.0)
    penalty_performance_warning: float = Field(default=2.0, ge=0.0, le=100.0)
    penalty_performance_suggestion: float = Field(default=1.0, ge=0.0, le=100.0)

    # Best Practices Issue Penalties
    penalty_best_practices_critical: float = Field(default=2.0, ge=0.0, le=100.0)
    penalty_best_practices_warning: float = Field(default=1.0, ge=0.0, le=100.0)
    penalty_best_practices_suggestion: float = Field(default=0.5, ge=0.0, le=100.0)

    # Code Style Issue Penalties (0 = informational only)
    penalty_code_style_critical: float = Field(default=0.0, ge=0.0, le=100.0)
    penalty_code_style_warning: float = Field(default=0.0, ge=0.0, le=100.0)
    penalty_code_style_suggestion: float = Field(default=0.0, ge=0.0, le=100.0)

    # Documentation Issue Penalties (0 = informational only)
    penalty_documentation_critical: float = Field(default=0.0, ge=0.0, le=100.0)
    penalty_documentation_warning: float = Field(default=0.0, ge=0.0, le=100.0)
    penalty_documentation_suggestion: float = Field(default=0.0, ge=0.0, le=100.0)

    # Star Rating Thresholds (penalty points)
    penalty_threshold_5_stars: float = Field(default=0.0, ge=0.0, le=1000.0)
    penalty_threshold_4_stars: float = Field(default=5.0, ge=0.0, le=1000.0)
    penalty_threshold_3_stars: float = Field(default=15.0, ge=0.0, le=1000.0)
    penalty_threshold_2_stars: float = Field(default=30.0, ge=0.0, le=1000.0)
    penalty_threshold_1_star: float = Field(default=50.0, ge=0.0, le=1000.0)

    @property
    def azure_devops_url(self) -> str:
        """Get Azure DevOps organization URL."""
        if not self.azure_devops_org:
            raise ValueError("AZURE_DEVOPS_ORG is not configured")
        return f"https://dev.azure.com/{self.azure_devops_org}"

    def get_azure_devops_token(self) -> str:
        """
        Get the appropriate Azure DevOps access token based on auth_mode.

        Returns:
            Access token string

        Raises:
            ValueError: If no valid token is available
        """
        if self.auth_mode == "pat":
            if not self.azure_devops_pat:
                raise ValueError(
                    "auth_mode is 'pat' but AZURE_DEVOPS_PAT is not configured."
                )
            return self.azure_devops_pat

        elif self.auth_mode == "system_token":
            if not self.azure_devops_system_token:
                raise ValueError(
                    "auth_mode is 'system_token' but AZURE_DEVOPS_SYSTEM_TOKEN is not available."
                )
            return self.azure_devops_system_token

        else:  # auto
            if self.azure_devops_system_token:
                return self.azure_devops_system_token
            elif self.azure_devops_pat:
                return self.azure_devops_pat
            else:
                raise ValueError(
                    "No Azure DevOps authentication configured. "
                    "Set AZURE_DEVOPS_PAT or AZURE_DEVOPS_SYSTEM_TOKEN."
                )

    def get_penalty_matrix(self) -> Dict[str, Dict[str, float]]:
        """Get penalty points matrix for all categories and severities."""
        return {
            'security': {
                'critical': self.penalty_security_critical,
                'warning': self.penalty_security_warning,
                'suggestion': self.penalty_security_suggestion,
                'good': 0.0
            },
            'performance': {
                'critical': self.penalty_performance_critical,
                'warning': self.penalty_performance_warning,
                'suggestion': self.penalty_performance_suggestion,
                'good': 0.0
            },
            'best_practices': {
                'critical': self.penalty_best_practices_critical,
                'warning': self.penalty_best_practices_warning,
                'suggestion': self.penalty_best_practices_suggestion,
                'good': 0.0
            },
            'code_style': {
                'critical': self.penalty_code_style_critical,
                'warning': self.penalty_code_style_warning,
                'suggestion': self.penalty_code_style_suggestion,
                'good': 0.0
            },
            'documentation': {
                'critical': self.penalty_documentation_critical,
                'warning': self.penalty_documentation_warning,
                'suggestion': self.penalty_documentation_suggestion,
                'good': 0.0
            }
        }

    def get_star_thresholds(self) -> List[float]:
        """Get star rating thresholds in ascending order."""
        return [
            self.penalty_threshold_5_stars,
            self.penalty_threshold_4_stars,
            self.penalty_threshold_3_stars,
            self.penalty_threshold_2_stars,
            self.penalty_threshold_1_star,
        ]


# Global settings instance
_settings: Settings | None = None


def get_settings(force_reload: bool = False) -> Settings:
    """
    Get the global settings instance.

    Args:
        force_reload: Force reload settings from environment

    Returns:
        Settings instance
    """
    global _settings

    if _settings is None or force_reload:
        _settings = Settings()

    return _settings


def reset_settings():
    """Reset the global settings instance (useful for testing)."""
    global _settings
    _settings = None
