# ui_charts.py
# Sistema de estilo de gráficas (Plotly) para PowerSync (Desktop)
# CS-007.1.1 — Hotfix compatibilidad (no rompe callbacks)

from __future__ import annotations
from typing import Optional, Callable
import plotly.graph_objects as go

_FONT_FAMILY = "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial"


def _safe(call: Callable, *args, **kwargs) -> None:
    """Aplica un update de estilo sin romper si la versión de Plotly no soporta alguna prop."""
    try:
        call(*args, **kwargs)
    except Exception:
        # Estilo opcional: si falla, degradamos sin afectar la figura ni el callback.
        return


def apply_chart_style(
    fig: go.Figure,
    *,
    title: Optional[str] = None,
    x_title: Optional[str] = None,
    y_title: Optional[str] = None,
    height: int = 420,
) -> go.Figure:
    """Aplica un estilo consistente y legible (desktop) sin cambiar la data."""

    # Base layout (muy compatible)
    base_layout = dict(
        height=height,
        margin=dict(l=44, r=16, t=52, b=44),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=_FONT_FAMILY, size=12, color="#E7ECF3"),
        legend=dict(orientation="h"),
    )
    _safe(fig.update_layout, **base_layout)

    if title is not None:
        _safe(
            fig.update_layout,
            title=dict(
                text=title,
                x=0.01,
                xanchor="left",
                y=0.98,
                yanchor="top",
                font=dict(size=16),
            ),
        )

    # Hover “pro” (si está disponible)
    _safe(fig.update_layout, hovermode="x unified")
    _safe(
        fig.update_layout,
        hoverlabel=dict(
            bgcolor="#0f1623",
            bordercolor="#2c3d55",
            font=dict(family=_FONT_FAMILY, size=12, color="#E7ECF3"),
        ),
    )

    # Mantén zoom/pan en interacciones (si aplica en tu versión)
    _safe(fig.update_layout, uirevision="powersync")

    if x_title is not None:
        _safe(fig.update_xaxes, title_text=x_title)
    if y_title is not None:
        _safe(fig.update_yaxes, title_text=y_title)

    axis_common = dict(
        showgrid=True,
        gridcolor="rgba(255,255,255,0.06)",
        zeroline=False,
        showline=True,
        linecolor="rgba(255,255,255,0.12)",
        ticks="outside",
        ticklen=4,
        tickcolor="rgba(255,255,255,0.18)",
        tickfont=dict(size=12),
        titlefont=dict(size=12, color="#C9D3E3"),
    )
    _safe(fig.update_xaxes, **axis_common)
    _safe(fig.update_yaxes, **axis_common)

    # Crosshair / spikes (opcional)
    _safe(
        fig.update_xaxes,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="solid",
        spikecolor="rgba(255,255,255,0.25)",
        spikethickness=1,
    )
    _safe(
        fig.update_yaxes,
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikedash="solid",
        spikecolor="rgba(255,255,255,0.25)",
        spikethickness=1,
    )

    # Ajuste de dominio (opcional)
    _safe(fig.update_layout, xaxis=dict(constrain="domain"))

    return fig


def graph_config() -> dict:
    """Config estándar para dcc.Graph (desktop)."""
    return {
        "displayModeBar": False,
        "responsive": True,
        "scrollZoom": True,
    }
