from __future__ import annotations

from html import escape
from typing import Iterable, Literal

import streamlit as st


Tone = Literal["success", "danger", "warning", "info", "neutral"]


def load_global_css() -> None:
    """Apply the shared Streamlit styling used across frontend pages."""
    st.markdown(
        """
        <style>
            :root {
                --pt-bg: #0b0f16;
                --pt-bg-soft: #10151f;
                --pt-surface: #151a24;
                --pt-surface-2: #1b202b;
                --pt-border: #303846;
                --pt-border-soft: rgba(148, 163, 184, 0.22);
                --pt-text: #f8fafc;
                --pt-muted: #9ca3af;
                --pt-subtle: #6b7280;
                --pt-accent: #ff4d57;
                --pt-accent-soft: rgba(255, 77, 87, 0.16);
                --pt-success: #00b894;
                --pt-success-soft: rgba(0, 184, 148, 0.14);
                --pt-danger: #ff4d43;
                --pt-danger-soft: rgba(255, 77, 67, 0.14);
                --pt-warning: #f4bf50;
                --pt-warning-soft: rgba(244, 191, 80, 0.14);
                --pt-info: #60a5fa;
                --pt-info-soft: rgba(96, 165, 250, 0.14);
                --pt-radius: 14px;
                --pt-shadow: 0 18px 50px rgba(0, 0, 0, 0.24);
            }

            [data-testid="stAppViewContainer"] {
                background:
                    radial-gradient(circle at top left, rgba(255, 77, 87, 0.08), transparent 28rem),
                    linear-gradient(135deg, #0b0f16 0%, #0d111a 45%, #090d14 100%);
                color: var(--pt-text);
            }

            [data-testid="stHeader"] {
                background: rgba(11, 15, 22, 0.72);
                backdrop-filter: blur(14px);
                border-bottom: 1px solid rgba(255, 77, 87, 0.22);
            }

            .block-container {
                max-width: 1480px;
                padding-top: 3.2rem;
                padding-bottom: 4rem;
            }

            [data-testid="stSidebar"] {
                background: #252833;
                border-right: 1px solid rgba(148, 163, 184, 0.16);
            }

            [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p,
            [data-testid="stSidebar"] label,
            [data-testid="stSidebar"] span {
                color: #d1d5db;
            }

            h1, h2, h3 {
                letter-spacing: 0;
                color: var(--pt-text);
            }

            p, label, span {
                color: inherit;
            }

            div[data-testid="stVerticalBlockBorderWrapper"] {
                border-color: var(--pt-border-soft) !important;
                border-radius: var(--pt-radius) !important;
                background: rgba(21, 26, 36, 0.68);
                box-shadow: 0 12px 30px rgba(0, 0, 0, 0.16);
            }

            div[data-testid="stForm"] {
                border: 1px solid var(--pt-border-soft);
                border-radius: var(--pt-radius);
                background: rgba(21, 26, 36, 0.72);
                padding: 1.15rem 1.2rem 1.35rem;
                box-shadow: 0 12px 30px rgba(0, 0, 0, 0.14);
            }

            div[data-testid="stMetric"] {
                border: 1px solid var(--pt-border-soft);
                border-radius: var(--pt-radius);
                background: linear-gradient(180deg, rgba(27, 32, 43, 0.9), rgba(18, 23, 32, 0.88));
                padding: 1rem 1.05rem;
                box-shadow: 0 12px 28px rgba(0, 0, 0, 0.14);
            }

            div[data-testid="stMetric"] label,
            div[data-testid="stMetric"] [data-testid="stMetricLabel"] {
                color: var(--pt-muted) !important;
                font-weight: 700;
                text-transform: uppercase;
                font-size: 0.72rem;
            }

            div[data-testid="stMetricValue"] {
                color: var(--pt-text);
                font-weight: 800;
            }

            .stTextInput input,
            .stNumberInput input,
            .stDateInput input,
            .stTextArea textarea,
            div[data-baseweb="select"] > div {
                border: 1px solid rgba(148, 163, 184, 0.2) !important;
                border-radius: 11px !important;
                background-color: #252833 !important;
                color: var(--pt-text) !important;
            }

            .stTextInput input:focus,
            .stNumberInput input:focus,
            .stDateInput input:focus,
            .stTextArea textarea:focus {
                border-color: rgba(255, 77, 87, 0.72) !important;
                box-shadow: 0 0 0 3px rgba(255, 77, 87, 0.12) !important;
            }

            .stButton > button,
            .stDownloadButton > button,
            button[kind="secondary"],
            button[kind="primary"] {
                border-radius: 11px !important;
                border: 1px solid rgba(148, 163, 184, 0.28) !important;
                background: rgba(20, 25, 35, 0.95) !important;
                color: var(--pt-text) !important;
                font-weight: 750 !important;
                min-height: 2.75rem;
                transition: border-color 120ms ease, transform 120ms ease, background 120ms ease;
            }

            .stButton > button:hover,
            .stDownloadButton > button:hover,
            button[kind="secondary"]:hover,
            button[kind="primary"]:hover {
                border-color: rgba(255, 77, 87, 0.72) !important;
                background: rgba(255, 77, 87, 0.12) !important;
                transform: translateY(-1px);
            }

            div[data-testid="stTabs"] button {
                color: #d1d5db;
                font-weight: 800;
            }

            div[data-testid="stTabs"] button[aria-selected="true"] {
                color: var(--pt-accent);
            }

            div[data-testid="stDataFrame"],
            div[data-testid="stTable"] {
                border: 1px solid var(--pt-border-soft);
                border-radius: var(--pt-radius);
                overflow: hidden;
                background: rgba(21, 26, 36, 0.72);
            }

            div[data-testid="stAlert"] {
                border-radius: var(--pt-radius);
                border: 1px solid rgba(148, 163, 184, 0.18);
            }

            .pt-page-header {
                margin-bottom: 1.4rem;
                padding-bottom: 0.85rem;
                border-bottom: 1px solid rgba(148, 163, 184, 0.16);
            }

            .pt-page-header-row {
                display: flex;
                align-items: flex-start;
                justify-content: space-between;
                gap: 1rem;
            }

            .pt-page-title {
                margin: 0;
                color: var(--pt-text);
                font-size: clamp(2.1rem, 4vw, 3.4rem);
                line-height: 1.04;
                font-weight: 900;
            }

            .pt-page-subtitle {
                max-width: 920px;
                margin: 0.75rem 0 0;
                color: var(--pt-muted);
                font-size: 1.02rem;
                line-height: 1.55;
            }

            .pt-card {
                border: 1px solid var(--pt-border-soft);
                border-radius: var(--pt-radius);
                background: linear-gradient(180deg, rgba(27, 32, 43, 0.92), rgba(17, 22, 31, 0.92));
                box-shadow: var(--pt-shadow);
            }

            .pt-section-card {
                padding: 1.15rem 1.2rem;
                margin: 0.8rem 0 1.05rem;
            }

            .pt-section-title {
                margin: 0;
                color: var(--pt-text);
                font-size: 1.18rem;
                font-weight: 850;
            }

            .pt-section-subtitle {
                margin: 0.35rem 0 0;
                color: var(--pt-muted);
                line-height: 1.5;
            }

            .pt-metric-grid {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
                gap: 0.85rem;
                margin: 0.85rem 0 1.1rem;
            }

            .pt-metric-card {
                padding: 1rem 1.05rem;
            }

            .pt-metric-label {
                color: var(--pt-muted);
                font-size: 0.76rem;
                font-weight: 800;
                text-transform: uppercase;
            }

            .pt-metric-value {
                color: var(--pt-text);
                margin-top: 0.45rem;
                font-size: clamp(1.35rem, 2.4vw, 2rem);
                font-weight: 900;
                line-height: 1.1;
            }

            .pt-metric-delta {
                margin-top: 0.45rem;
                font-weight: 850;
                font-size: 0.92rem;
            }

            .pt-tone-success { color: var(--pt-success) !important; }
            .pt-tone-danger { color: var(--pt-danger) !important; }
            .pt-tone-warning { color: var(--pt-warning) !important; }
            .pt-tone-info { color: var(--pt-info) !important; }
            .pt-tone-neutral { color: var(--pt-muted) !important; }

            .pt-badge {
                display: inline-flex;
                align-items: center;
                gap: 0.35rem;
                border-radius: 999px;
                padding: 0.35rem 0.62rem;
                font-size: 0.78rem;
                font-weight: 850;
                border: 1px solid rgba(148, 163, 184, 0.18);
                white-space: nowrap;
            }

            .pt-badge-success { background: var(--pt-success-soft); color: var(--pt-success); border-color: rgba(0, 184, 148, 0.28); }
            .pt-badge-danger { background: var(--pt-danger-soft); color: var(--pt-danger); border-color: rgba(255, 77, 67, 0.28); }
            .pt-badge-warning { background: var(--pt-warning-soft); color: var(--pt-warning); border-color: rgba(244, 191, 80, 0.28); }
            .pt-badge-info { background: var(--pt-info-soft); color: var(--pt-info); border-color: rgba(96, 165, 250, 0.28); }
            .pt-badge-neutral { background: rgba(148, 163, 184, 0.12); color: #d1d5db; }

            .pt-banner {
                border-radius: var(--pt-radius);
                padding: 0.9rem 1rem;
                margin: 0.75rem 0 1rem;
                border: 1px solid rgba(148, 163, 184, 0.18);
                font-weight: 650;
                line-height: 1.5;
            }

            .pt-banner-info { background: var(--pt-info-soft); color: #cfe6ff; border-color: rgba(96, 165, 250, 0.24); }
            .pt-banner-success { background: var(--pt-success-soft); color: #bcf7e9; border-color: rgba(0, 184, 148, 0.24); }
            .pt-banner-warning { background: var(--pt-warning-soft); color: #fff0c2; border-color: rgba(244, 191, 80, 0.24); }
            .pt-banner-danger { background: var(--pt-danger-soft); color: #ffd4d1; border-color: rgba(255, 77, 67, 0.24); }
            .pt-banner-neutral { background: rgba(148, 163, 184, 0.1); color: #d1d5db; }

            .pt-empty {
                padding: 1.4rem;
                text-align: left;
            }

            .pt-empty-title {
                color: var(--pt-text);
                font-size: 1.08rem;
                font-weight: 850;
                margin: 0;
            }

            .pt-empty-message {
                color: var(--pt-muted);
                margin: 0.4rem 0 0;
                line-height: 1.55;
            }

            @media (max-width: 900px) {
                .block-container {
                    padding-left: 1rem;
                    padding-right: 1rem;
                }

                .pt-page-header-row {
                    flex-direction: column;
                }

                .pt-metric-grid {
                    grid-template-columns: 1fr;
                }
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def status_badge(text: str, tone: Tone = "neutral", *, render: bool = False) -> str:
    html = f'<span class="pt-badge pt-badge-{tone}">{escape(str(text))}</span>'
    if render:
        st.markdown(html, unsafe_allow_html=True)
    return html


def page_header(title: str, subtitle: str | None = None, right_badge: str | None = None) -> None:
    badge = f"<div>{right_badge}</div>" if right_badge else ""
    subtitle_html = f'<p class="pt-page-subtitle">{escape(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f"""
        <div class="pt-page-header">
            <div class="pt-page-header-row">
                <div>
                    <h1 class="pt-page-title">{escape(title)}</h1>
                    {subtitle_html}
                </div>
                {badge}
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def section_card(title: str | None = None, subtitle: str | None = None) -> None:
    title_html = f'<h3 class="pt-section-title">{escape(title)}</h3>' if title else ""
    subtitle_html = f'<p class="pt-section-subtitle">{escape(subtitle)}</p>' if subtitle else ""
    st.markdown(
        f'<div class="pt-card pt-section-card">{title_html}{subtitle_html}</div>',
        unsafe_allow_html=True,
    )


def _delta_tone(delta: str | None, fallback: Tone = "neutral") -> Tone:
    if not delta:
        return fallback
    clean_delta = delta.strip()
    if clean_delta.startswith(("+", "↑")):
        return "success"
    if clean_delta.startswith(("-", "↓")):
        return "danger"
    return fallback


def metric_card(label: str, value: object, delta: object | None = None, status: Tone | None = None) -> None:
    tone = status or _delta_tone(str(delta) if delta is not None else None)
    delta_html = (
        f'<div class="pt-metric-delta pt-tone-{tone}">{escape(str(delta))}</div>' if delta is not None else ""
    )
    st.markdown(
        f"""
        <div class="pt-card pt-metric-card">
            <div class="pt-metric-label">{escape(label)}</div>
            <div class="pt-metric-value">{escape(str(value))}</div>
            {delta_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_grid(metrics: Iterable[dict[str, object]], columns: int | None = None) -> None:
    metrics_list = list(metrics)
    if not metrics_list:
        return
    grid_columns = columns or min(len(metrics_list), 4)
    cols = st.columns(grid_columns)
    for index, metric in enumerate(metrics_list):
        with cols[index % grid_columns]:
            metric_card(
                str(metric.get("label", "")),
                metric.get("value", ""),
                metric.get("delta"),
                metric.get("status"),  # type: ignore[arg-type]
            )


def info_banner(message: str, tone: Tone = "info") -> None:
    st.markdown(
        f'<div class="pt-banner pt-banner-{tone}">{escape(message)}</div>',
        unsafe_allow_html=True,
    )


def empty_state(title: str, message: str, action_text: str | None = None) -> None:
    action_html = (
        f'<p class="pt-empty-message"><strong>{escape(action_text)}</strong></p>' if action_text else ""
    )
    st.markdown(
        f"""
        <div class="pt-card pt-empty">
            <p class="pt-empty-title">{escape(title)}</p>
            <p class="pt-empty-message">{escape(message)}</p>
            {action_html}
        </div>
        """,
        unsafe_allow_html=True,
    )
