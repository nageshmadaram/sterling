from pydantic import field_validator
from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):
    environment: str = "development"
    paper_trading: bool = True
    real_public_data: bool = True
    default_underlying: str = "NIFTY"
    log_level: str = "INFO"
    log_json: bool = False  # opt-in structured JSON logging (Phase 2 observability)
    database_url: str = ""       # SQLAlchemy URL; empty → dedicated sqlite file (Postgres-ready)
    use_sqlalchemy: bool = False  # Phase 5 dual-write flag (default OFF)
    enable_event_bus: bool = False  # Phase 3 live event bus + agents wiring (default OFF)
    cors_origins: List[str] = ["http://localhost:5173", "http://localhost:3000"]
    exchange_adapter: str = "zerodha"  # the only adapter in this build

    # Comma-separated strategies allowed to ORIGINATE new exposure. Legacy
    # engines keep managing, reconciling and exiting what they already hold;
    # see app.core.focus. Blank = the declared default (snapback,supertrend).
    sterling_focused_strategies: str = ""

    max_contracts: int = 10
    max_position_pct: float = 0.05
    default_capital: float = 100_000.0

    # TrueData market-data credentials/configuration. Credentials are loaded
    # from environment/.env and are never committed to the repository.
    truedata_username: str = ""
    truedata_password: str = ""
    truedata_auth_url: str = "https://auth.truedata.in/token"
    truedata_history_base_url: str = "https://history.truedata.in"
    truedata_market_api_base_url: str = "https://api.truedata.in"
    truedata_timeout_seconds: float = 30.0

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors(cls, v):
        if isinstance(v, str):
            stripped = v.strip()
            if stripped.startswith("["):
                import json
                try:
                    return json.loads(stripped)
                except Exception:
                    pass
            return [s.strip().strip('"\'') for s in stripped.split(",") if s.strip()]
        return v

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
