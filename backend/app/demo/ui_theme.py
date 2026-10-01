"""AAXIS AI visual identity for the workbench: dark graphite + brushed silver, matching the logo.

Only presentation lives here (theme variables, CSS, fonts, logo assets). No coding logic.
"""

from __future__ import annotations

import base64
from functools import lru_cache
from pathlib import Path

ASSETS = Path(__file__).resolve().parent / "assets"
BRAND = "AAXIS AI"
BRAND_SUB = "Automations"
PRODUCT = "Medical Coding Intelligence"


@lru_cache
def data_uri(name: str) -> str:
    p = ASSETS / name
    if not p.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()


def asset_path(name: str) -> str | None:
    p = ASSETS / name
    return str(p) if p.exists() else None


# Palette -------------------------------------------------------------------------------
BG = "#08080a"
SURFACE = "#101013"
SURFACE_2 = "#16161a"
SURFACE_3 = "#1d1d22"
BORDER = "#26262d"
BORDER_STRONG = "#3a3a43"
TEXT = "#ececf0"
MUTED = "#a2a2ab"
FAINT = "#6e6e78"
SILVER = "#d7d8de"


def theme():
    """Gradio theme with identical light/dark values, so the brand look never flips with the OS."""
    import gradio as gr

    base = gr.themes.Base(
        primary_hue=gr.themes.colors.neutral,
        secondary_hue=gr.themes.colors.neutral,
        neutral_hue=gr.themes.colors.neutral,
        font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
        font_mono=[gr.themes.GoogleFont("JetBrains Mono"), "ui-monospace", "monospace"],
        radius_size=gr.themes.sizes.radius_md,
    )
    values = {
        "body_background_fill": BG,
        "body_text_color": TEXT,
        "body_text_color_subdued": MUTED,
        "background_fill_primary": SURFACE,
        "background_fill_secondary": SURFACE_2,
        "block_background_fill": SURFACE,
        "block_border_color": BORDER,
        "block_label_text_color": MUTED,
        "block_label_background_fill": SURFACE,
        "block_title_text_color": MUTED,
        "block_title_background_fill": "transparent",
        "block_shadow": "none",
        "panel_background_fill": SURFACE,
        "border_color_primary": BORDER,
        "input_background_fill": SURFACE_2,
        "input_border_color": BORDER_STRONG,
        "input_border_color_focus": "#8d8e96",
        "input_placeholder_color": FAINT,
        "input_shadow": "none",
        "button_primary_background_fill": "linear-gradient(180deg,#f4f4f6 0%,#c9cad0 55%,#9fa0a8 100%)",
        "button_primary_background_fill_hover": "linear-gradient(180deg,#ffffff 0%,#d8d9de 55%,#aeafb6 100%)",
        "button_primary_text_color": "#0b0b0d",
        "button_primary_border_color": "#c9cad0",
        "button_secondary_background_fill": SURFACE_3,
        "button_secondary_text_color": TEXT,
        "button_secondary_border_color": BORDER_STRONG,
        "color_accent": SILVER,
        "color_accent_soft": SURFACE_3,
        "checkbox_background_color": SURFACE_2,
        "checkbox_background_color_selected": SILVER,
        "checkbox_border_color": BORDER_STRONG,
        "checkbox_label_background_fill": SURFACE_2,
        "checkbox_label_background_fill_selected": SURFACE_3,
        "checkbox_label_border_color": BORDER_STRONG,
        "checkbox_label_text_color": MUTED,
        "checkbox_label_text_color_selected": TEXT,
        "table_even_background_fill": SURFACE,
        "table_odd_background_fill": SURFACE_2,
        "table_border_color": BORDER,
        "table_row_focus": SURFACE_3,
        "link_text_color": SILVER,
        "code_background_fill": SURFACE_2,
        "loader_color": SILVER,
        "slider_color": SILVER,
        "stat_background_fill": SURFACE_3,
        "accordion_text_color": TEXT,
    }
    both = {}
    for k, v in values.items():
        if hasattr(base, k):
            both[k] = v
        if hasattr(base, f"{k}_dark"):
            both[f"{k}_dark"] = v
    return base.set(**both)


HEAD = """
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@500;600&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
<meta name="theme-color" content="#08080a">
"""

CSS = r"""
:root{
  --ax-bg:#08080a; --ax-surface:#101013; --ax-surface-2:#16161a; --ax-surface-3:#1d1d22;
  --ax-border:#26262d; --ax-border-strong:#3a3a43; --ax-text:#ececf0; --ax-muted:#a2a2ab; --ax-faint:#6e6e78;
  --ax-silver:#d7d8de;
  --ax-metal:linear-gradient(180deg,#fafafc 0%,#cfd0d6 45%,#9d9ea6 100%);
  --ax-ok:#3fbf8f; --ax-ok-bg:rgba(63,191,143,.12);
  --ax-warn:#e6ad45; --ax-warn-bg:rgba(230,173,69,.12);
  --ax-deny:#f0676a; --ax-deny-bg:rgba(240,103,106,.12);
  --ax-info:#7fa8d6; --ax-info-bg:rgba(127,168,214,.12);
  --ax-display:'Cinzel','Times New Roman',serif;
  --ax-sans:'Inter',ui-sans-serif,system-ui,sans-serif;
  --ax-mono:'JetBrains Mono',ui-monospace,monospace;
}
body, gradio-app, .gradio-container{ background:var(--ax-bg) !important; }
body{ background: radial-gradient(1200px 520px at 50% -140px, #1b1b21 0%, var(--ax-bg) 70%) fixed !important; }
.gradio-container{ max-width:1320px !important; margin:0 auto !important; padding:0 20px 40px !important; font-family:var(--ax-sans) !important; }
footer{ display:none !important; }

/* ---------- header ---------- */
.ax-header{ display:flex; align-items:center; gap:24px; padding:22px 4px 18px; border-bottom:1px solid var(--ax-border); }
.ax-brand{ display:flex; align-items:center; gap:16px; min-width:0; }
.ax-brand .mark{ height:58px; width:auto; filter:drop-shadow(0 0 18px rgba(215,216,222,.08)); }
.ax-brand .word{ height:30px; width:auto; display:block; }
.ax-divider{ width:1px; align-self:stretch; background:var(--ax-border); margin:6px 4px; }
.ax-product{ display:flex; flex-direction:column; gap:4px; min-width:0; }
.ax-product .name{ font-family:var(--ax-display); font-size:19px; letter-spacing:.14em; text-transform:uppercase;
  color:#e6e7ec !important; text-shadow:0 1px 0 rgba(255,255,255,.08), 0 0 22px rgba(215,216,222,.12); line-height:1.25; }
.ax-product .tag{ color:var(--ax-muted); font-size:13px; }
.ax-head-meta{ margin-left:auto; display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.ax-chip{ display:inline-flex; align-items:center; gap:6px; padding:5px 10px; border:1px solid var(--ax-border-strong);
  border-radius:999px; font-size:11.5px; letter-spacing:.06em; text-transform:uppercase; color:var(--ax-muted); background:var(--ax-surface); white-space:nowrap; }
.ax-chip .dot{ width:6px; height:6px; border-radius:50%; background:var(--ax-ok); box-shadow:0 0 8px var(--ax-ok); }
.ax-flow{ display:flex; gap:0; flex-wrap:wrap; margin:14px 0 6px; color:var(--ax-faint); font-size:12px; letter-spacing:.08em; text-transform:uppercase; }
.ax-flow span{ display:flex; align-items:center; }
.ax-flow span:not(:last-child)::after{ content:""; width:22px; height:1px; background:var(--ax-border-strong); margin:0 10px; }
.ax-flow b{ color:var(--ax-silver); font-weight:600; margin-right:6px; font-family:var(--ax-mono); }

/* ---------- tabs ---------- */
.ax-tabs > .tab-wrapper, .ax-tabs .tab-container{ border-bottom:1px solid var(--ax-border) !important; }
.ax-tabs button[role="tab"]{ font-size:12.5px !important; letter-spacing:.12em !important; text-transform:uppercase !important;
  color:var(--ax-muted) !important; padding:12px 16px !important; font-weight:500 !important; }
.ax-tabs button[role="tab"][aria-selected="true"], .ax-tabs button[role="tab"].selected{ color:var(--ax-text) !important; }
.ax-tabs button[role="tab"][aria-selected="true"]::after, .ax-tabs button[role="tab"].selected::after{ background:var(--ax-silver) !important; height:2px !important; }

/* ---------- gradio blocks polish ---------- */
.ax-panel{ border:1px solid var(--ax-border) !important; border-radius:14px !important; background:var(--ax-surface) !important; padding:14px !important; }
.ax-panel .block, .ax-panel .form{ background:transparent !important; border:none !important; box-shadow:none !important; }
textarea, input[type=text], input[type=number]{ font-family:var(--ax-sans) !important; }
.ax-note textarea{ font-family:var(--ax-mono) !important; font-size:12.5px !important; line-height:1.55 !important; }
.ax-primary{ font-weight:700 !important; letter-spacing:.08em !important; text-transform:uppercase !important; font-size:13px !important;
  box-shadow:0 8px 24px rgba(215,216,222,.10) !important; min-height:46px !important; }
.ax-download{ min-height:40px !important; }
.ax-lookup-dl{ max-width:320px; }
.ax-download button, button.ax-download{ letter-spacing:.06em !important; text-transform:uppercase !important; font-size:12px !important; }
.ax-hint{ color:var(--ax-faint); font-size:12px; margin:2px 2px 0; }

/* ---------- result canvas ---------- */
.ax{ font-family:var(--ax-sans); color:var(--ax-text); font-size:14px; line-height:1.5; }
.ax *{ box-sizing:border-box; }
.ax div, .ax p, .ax span, .ax ul, .ax li{ max-width:none !important; }
.ax h3.sec{ font-family:var(--ax-display); font-weight:600; font-size:14px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--ax-silver); margin:26px 0 12px; display:flex; align-items:center; gap:12px; }
.ax h3.sec::after{ content:""; flex:1; height:1px; background:linear-gradient(90deg,var(--ax-border-strong),transparent); }
.ax h3.sec .count{ font-family:var(--ax-mono); font-size:11px; color:var(--ax-faint); letter-spacing:0; }
.ax .card{ background:var(--ax-surface); border:1px solid var(--ax-border); border-radius:14px; padding:16px 18px; }
.ax .muted{ color:var(--ax-muted); } .ax .faint{ color:var(--ax-faint); }
.ax .mono{ font-family:var(--ax-mono); }

.ax .empty{ border:1px dashed var(--ax-border-strong); border-radius:14px; padding:36px 24px; text-align:center; color:var(--ax-muted); background:rgba(16,16,19,.6); }
.ax .empty img{ height:54px; opacity:.55; margin-bottom:10px; }
.ax .empty .t{ font-family:var(--ax-display); letter-spacing:.14em; text-transform:uppercase; color:var(--ax-silver); font-size:14px; margin-bottom:6px; }
.ax .error{ border:1px solid rgba(240,103,106,.45); background:var(--ax-deny-bg); border-radius:12px; padding:14px 16px; color:#ffd4d5; }

/* KPI tiles */
.ax .kpis{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-top:6px; }
.ax .kpi{ background:var(--ax-surface); border:1px solid var(--ax-border); border-radius:14px; padding:14px 16px; position:relative; overflow:hidden; }
.ax .kpi::before{ content:""; position:absolute; inset:0 0 auto 0; height:2px; background:var(--ax-border-strong); }
.ax .kpi.ok::before{ background:var(--ax-ok);} .ax .kpi.warn::before{ background:var(--ax-warn);} .ax .kpi.deny::before{ background:var(--ax-deny);}
.ax .kpi .l{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ax-faint); }
.ax .kpi .v{ font-size:24px; font-weight:700; margin-top:4px; letter-spacing:-.01em; }
.ax .kpi .s{ font-size:12px; color:var(--ax-muted); margin-top:2px; }
.ax .kpi.ok .v{ color:var(--ax-ok);} .ax .kpi.warn .v{ color:var(--ax-warn);} .ax .kpi.deny .v{ color:var(--ax-deny);}
.ax .meter{ height:6px; border-radius:6px; background:var(--ax-surface-3); margin-top:8px; overflow:hidden; }
.ax .meter i{ display:block; height:100%; border-radius:6px; }

/* tables */
.ax .tbl{ border:1px solid var(--ax-border); border-radius:14px; overflow:hidden; overflow-x:auto; background:var(--ax-surface); }
.ax table.t{ width:100% !important; border-collapse:collapse !important; border-spacing:0 !important; border:none !important; margin:0 !important; background:transparent !important; }
.ax table.t th, .ax table.t td, .ax table.t tr, .ax table.t thead, .ax table.t tbody{ border:none !important; }
.ax table.t th{ text-align:left; font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ax-faint); font-weight:600;
  padding:11px 14px; background:var(--ax-surface-2); border-bottom:1px solid var(--ax-border); }
.ax table.t td{ padding:12px 14px; border-bottom:1px solid var(--ax-border) !important; vertical-align:top; background:transparent !important; color:var(--ax-text); }
.ax table.t th{ border-bottom:1px solid var(--ax-border) !important; }
.ax table.t tr:last-child td{ border-bottom:none !important; }
.ax table.t tr:hover td{ background:rgba(255,255,255,.015); }
.ax .code{ font-family:var(--ax-mono); font-weight:600; font-size:14px; color:#fff; letter-spacing:.02em; white-space:nowrap; }
.ax .desc{ font-weight:500; }
.ax .ev{ margin-top:5px; font-size:12.5px; color:var(--ax-muted); border-left:2px solid var(--ax-border-strong); padding-left:8px; }
.ax .iss{ margin-top:5px; font-size:12.5px; color:var(--ax-warn); }
.ax .num{ font-family:var(--ax-mono); color:var(--ax-faint); }

/* pills */
.ax .pill{ display:inline-flex; align-items:center; gap:5px; padding:3px 9px; border-radius:999px; font-size:11px; font-weight:600;
  letter-spacing:.06em; text-transform:uppercase; border:1px solid var(--ax-border-strong); color:var(--ax-muted); white-space:nowrap; background:var(--ax-surface-2); }
.ax .pill.ok{ color:var(--ax-ok); border-color:rgba(63,191,143,.4); background:var(--ax-ok-bg); }
.ax .pill.warn{ color:var(--ax-warn); border-color:rgba(230,173,69,.4); background:var(--ax-warn-bg); }
.ax .pill.deny{ color:var(--ax-deny); border-color:rgba(240,103,106,.45); background:var(--ax-deny-bg); }
.ax .pill.info{ color:var(--ax-info); border-color:rgba(127,168,214,.4); background:var(--ax-info-bg); }
.ax .sys{ display:inline-block; font-family:var(--ax-mono); font-size:10.5px; padding:2px 7px; border-radius:6px; border:1px solid var(--ax-border-strong); color:var(--ax-silver); background:var(--ax-surface-3); white-space:nowrap; }
.ax .sys.dx{ border-color:#4b5a6e; color:#b9cbe3; } .ax .sys.px{ border-color:#5d5470; color:#d2c4ee; } .ax .sys.hc{ border-color:#4e6a60; color:#bfe3d4; }

.ax .conf{ min-width:110px; }
.ax .conf .bar{ height:5px; border-radius:5px; background:var(--ax-surface-3); margin-top:6px; overflow:hidden; }
.ax .conf .bar i{ display:block; height:100%; background:var(--ax-metal); border-radius:5px; }
.ax .conf .pct{ font-family:var(--ax-mono); font-size:13px; }

/* grid for claim */
.ax .two{ display:grid; grid-template-columns: minmax(0,1fr) minmax(0,1.35fr); gap:14px; }
.ax .dxlist{ display:flex; flex-direction:column; gap:8px; }
.ax .dx{ display:flex; gap:10px; align-items:baseline; }
.ax .dx .ptr{ font-family:var(--ax-mono); font-size:11px; color:var(--ax-faint); width:18px; }

/* issues */
.ax .issues{ display:flex; flex-direction:column; gap:10px; }
.ax .issue{ display:grid; grid-template-columns:92px minmax(0,1fr) auto; gap:14px; align-items:start; padding:13px 16px; border:1px solid var(--ax-border); border-radius:12px; background:var(--ax-surface); }
.ax .issue.deny{ border-left:3px solid var(--ax-deny);} .ax .issue.review{ border-left:3px solid var(--ax-warn);} .ax .issue.info{ border-left:3px solid var(--ax-info);}
.ax .issue .msg{ font-weight:500; } .ax .issue .fix{ font-size:12.5px; color:var(--ax-muted); margin-top:4px; }
.ax .issue .src{ font-size:11.5px; color:var(--ax-faint); margin-top:3px; }
.ax .carc{ font-family:var(--ax-mono); font-size:11.5px; color:var(--ax-silver); border:1px solid var(--ax-border-strong); padding:3px 8px; border-radius:6px; white-space:nowrap; }
.ax .okbox{ border:1px solid rgba(63,191,143,.35); background:var(--ax-ok-bg); color:#bdf0d9; border-radius:12px; padding:13px 16px; }
.ax .notes{ margin-top:10px; font-size:12.5px; color:var(--ax-faint); }
.ax .notes li{ margin:3px 0; }

/* citations */
.ax details.cite{ border:1px solid var(--ax-border); border-radius:12px; background:var(--ax-surface); margin-bottom:8px; }
.ax details.cite summary{ cursor:pointer; list-style:none; padding:12px 16px; display:flex; gap:12px; align-items:center; }
.ax details.cite summary::-webkit-details-marker{ display:none; }
.ax details.cite summary::after{ content:"+"; margin-left:auto; color:var(--ax-faint); font-family:var(--ax-mono); }
.ax details.cite[open] summary::after{ content:"–"; }
.ax details.cite .ref{ font-size:12.5px; color:var(--ax-muted); }
.ax details.cite .body{ padding:0 16px 14px; color:var(--ax-muted); font-size:13px; border-top:1px solid var(--ax-border); padding-top:12px; }

/* entities */
.ax .ents{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }
.ax .chips{ display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
.ax .chip{ font-size:12.5px; padding:4px 10px; border-radius:8px; background:var(--ax-surface-2); border:1px solid var(--ax-border); }
.ax .chip.neg{ text-decoration:line-through; text-decoration-color:rgba(240,103,106,.8); color:var(--ax-muted); }
.ax .chip small{ color:var(--ax-faint); margin-left:6px; font-size:10.5px; text-transform:uppercase; letter-spacing:.06em; }
.ax .lbl{ font-size:11px; letter-spacing:.12em; text-transform:uppercase; color:var(--ax-faint); }

/* guideline search / lookup */
.ax .gl{ border:1px solid var(--ax-border); border-radius:14px; background:var(--ax-surface); padding:16px 18px; margin-bottom:10px; }
.ax .gl .h{ display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:8px; }
.ax .gl .title{ font-weight:600; }
.ax .gl .txt{ color:var(--ax-muted); font-size:13.5px; white-space:pre-wrap; }
.ax .detail{ display:grid; grid-template-columns:minmax(0,1fr); gap:10px; }
.ax .kv{ display:grid; grid-template-columns:170px minmax(0,1fr); gap:6px 14px; font-size:13px; }
.ax .kv .k{ color:var(--ax-faint); text-transform:uppercase; font-size:11px; letter-spacing:.1em; padding-top:2px; }
.ax .big-code{ font-family:var(--ax-mono); font-size:30px; font-weight:600; color:#f1f1f4 !important; letter-spacing:.02em; text-shadow:0 0 24px rgba(215,216,222,.15); }
.ax .foot{ margin-top:22px; font-size:11.5px; color:var(--ax-faint); display:flex; gap:14px; flex-wrap:wrap; }

.ax-footer{ margin-top:30px; padding-top:16px; border-top:1px solid var(--ax-border); display:flex; align-items:center; gap:12px; color:var(--ax-faint); font-size:12px; }
.ax-footer img{ height:22px; opacity:.8; }

html, body{ overflow-x:hidden; }
@media (max-width: 900px){
  .ax .kpis{ grid-template-columns:repeat(2,minmax(0,1fr)); }
  .ax .two, .ax .ents{ grid-template-columns:1fr; }
  .ax-header{ flex-wrap:wrap; gap:14px; } .ax-divider{ display:none; }
  .ax-product{ flex:1 1 100%; } .ax-head-meta{ margin-left:0; justify-content:flex-start; flex:1 1 100%; }
  .ax .issue{ grid-template-columns:1fr; }
  .ax-flow{ display:none; }
}
@media (max-width: 640px){
  .gradio-container{ padding:0 12px 30px !important; }
  .ax-product .name{ font-size:15px; } .ax-brand .mark{ height:46px; } .ax-brand .word{ height:24px; }
  .ax .kpi .v{ font-size:20px; }
  .ax-tabs button[role="tab"]{ padding:10px 10px !important; letter-spacing:.06em !important; }
  .ax .kv{ grid-template-columns:1fr; }
}
"""
