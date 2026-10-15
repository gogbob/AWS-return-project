"""
config.py — Centralised settings for the ALDO Returns Detective Agent.

All values are loaded from environment variables (or a .env file at the
project root). Defaults match the values documented in .env.example.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration loaded from environment / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── LLM / Model ──────────────────────────────────────────────────────────
    llm_mock: bool = Field(False, description="Use a mock LLM instead of a real provider")
    model_provider: str = Field("bedrock", description="LLM provider: 'bedrock' | 'openai'")
    model_id: str = Field("", description="Model ID / ARN for the chosen provider")
    aws_region: str = Field("", description="AWS region for Bedrock calls")

    # ── Item grouping key ─────────────────────────────────────────────────────
    # "name" → orderitem_product_name (style-level, recommended per data profile)
    # "sku"  → variant-level (size/colour); use only for variant-level analysis
    item_key: str = Field("name", description="Column used to group items: 'name' or 'sku'")

    # ── Loyalty simulation ────────────────────────────────────────────────────
    loyalty_seed: int = Field(42, description="Random seed for loyalty-score simulation")
    high_loyalty_threshold: int = Field(
        70, description="Percentile above which a customer is considered high-loyalty"
    )

    # ── Triage / scoring thresholds ───────────────────────────────────────────
    suspicion_threshold: float = Field(
        0.50, description="Composite triage score above which a return is 'suspicious'"
    )

    # ── Value thresholds (CAD) ────────────────────────────────────────────────
    high_value_cad: float = Field(150.0, description="Refund amount above which a return is 'high-value'")
    shipping_fee_cad: float = Field(7.99, description="Standard shipping fee to factor into net-cost calculations")

    # ── Statistical flagging parameters ──────────────────────────────────────
    min_returns_baseline: int = Field(
        30, description="Minimum resolved returns to compute a reliable baseline rate"
    )
    min_returns_flag: int = Field(
        30, description="Minimum returns for a customer/item to be eligible for flagging"
    )
    min_customers_flag: int = Field(
        20, description="Minimum unique customers before an item-level flag is raised"
    )
    flag_min_excess: float = Field(
        0.15, description="Minimum absolute excess return-rate above baseline to flag"
    )
    flag_min_ratio: float = Field(
        1.5, description="Minimum ratio of observed / expected return rate to flag"
    )
    flag_p_adj: float = Field(
        0.01, description="Benjamini-Hochberg adjusted p-value threshold for statistical flags"
    )

    # ── Size-guidance automation ──────────────────────────────────────────────
    auto_apply_size_guidance: bool = Field(
        False, description="Automatically apply size-guidance recommendations without manual review"
    )

    # ── Demo / testing ────────────────────────────────────────────────────────
    demo_item_name: str = Field("", description="Pre-populate the demo UI with this product name")

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = Field(
        "sqlite:///c:/AWS/AWS-return-project/backend/aldo_returns.db",
        description="SQLAlchemy database URL",
    )


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

settings = Settings()

# Triage composite-score weights (must sum to 1.0).
# loyalty:       how loyal the customer is (high loyalty → lower risk weight)
# implausibility: how implausible the return reason / pattern is
# claim_risk:    inherent risk level of the return reason group
# value_risk:    whether the refund amount is high
TRIAGE_WEIGHTS: dict[str, float] = {
    "loyalty": 0.40,
    "implausibility": 0.30,
    "claim_risk": 0.20,
    "value_risk": 0.10,
}

# Risk weight by normalised reason group.
# Groups map multiple raw `reason` values (see data_profile.md) to a
# canonical category; weights reflect claim-fraud likelihood.
CLAIM_RISK_BY_GROUP: dict[str, float] = {
    "DEFECT": 1.0,       # Defective / Manufacturer defect / Poor quality — hardest to verify
    "DESCRIPTION": 0.5,  # Not as advertised / Not what I expected / Wrong item
    "OTHER": 0.5,        # Changed my mind / Gift return / Found something else / etc.
    "FIT_SIZE": 0.3,     # Too big / Too small / Uncomfortable
    "PREFERENCE": 0.2,   # Not satisfied with colour / style preference
}
