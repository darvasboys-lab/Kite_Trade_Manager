"""Single-click column auto-fit shared across all strategy-builder dataframes.

Streamlit supports width=None for content-based column sizing. We preserve
all existing number formats, help tooltips and values while clearing explicit
widths for every configured column. Incrementing the table key forces fresh
grid sizing even if the user manually resized a column in an earlier render.
No network, browser JS, data or broker changes.
"""
from __future__ import annotations


def autofit_column_config(config: dict | None, enabled: bool) -> dict | None:
    """Return a new config with every configured width set to content-fit."""
    if config is None or not enabled:
        return config
    return {
        column: {**spec, "width": None} if isinstance(spec, dict) else spec
        for column, spec in config.items()
    }


def dataframe_key(name: str, state) -> str:
    """A new key remounts the grid and resets earlier manual drag widths."""
    revision = int(state.get("ccs_autofit_revision", 0))
    return f"{name}_autofit_{revision}"


def click_autofit(state) -> None:
    """One click applies content-fit widths to every strategy-builder table."""
    state["ccs_autofit_all"] = True
    state["ccs_autofit_revision"] = int(
        state.get("ccs_autofit_revision", 0)
    ) + 1


def autofit_enabled(state) -> bool:
    return bool(state.get("ccs_autofit_all", False))
