"""Central configuration, loaded from environment variables / .env."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

# Models the project is allowed to call. Anything else is refused,
# so a typo or copy-paste in .env can't silently switch to an expensive model.
ALLOWED_MODELS = {
    "claude-haiku-4-5-20251001",
}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    anthropic_model: str
    github_token: str | None
    database_url: str


def get_settings() -> Settings:
    model = os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    if model not in ALLOWED_MODELS:
        raise ValueError(
            f"Model '{model}' is not in ALLOWED_MODELS. "
            "Add it to src/repo_scorer/config.py if you really want to use it."
        )
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
        anthropic_model=model,
        github_token=os.getenv("GITHUB_TOKEN"),
        database_url=os.getenv(
            "DATABASE_URL",
            "postgresql+psycopg://scorer:scorer@localhost:5433/scorer",
        ),
    )
