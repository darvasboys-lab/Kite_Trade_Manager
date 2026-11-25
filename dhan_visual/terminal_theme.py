"""Approved terminal presentation: no data, broker or scanner dependencies."""
import streamlit as st

DARK = {
    "page": "#10151c", "surface": "#171e27", "surface2": "#1c2530",
    "border": "#2b3643", "text": "#e7edf4", "muted": "#8d9cad",
    "accent": "#48c9a1", "tint": "#183b33", "blue": "#80b8f3",
    "green": "#48c9a1", "red": "#f08b91",
}
LIGHT = {
    "page": "#eef2f6", "surface": "#ffffff", "surface2": "#f5f7fa",
    "border": "#dce3eb", "text": "#1c2938", "muted": "#607387",
    "accent": "#08795b", "tint": "#dff3e9", "blue": "#1769bc",
    "green": "#08795b", "red": "#b9404d",
}

CSS = """
:root { --rose:var(--accent); --rose-tint:var(--tint); --rose-border:var(--border); }
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"], [data-testid="stHeader"] {
  background:var(--page) !important; color:var(--text) !important;
}
[data-testid="stSidebar"] { background:var(--surface) !important; }
.block-container { max-width:1536px !important; padding:1.15rem 1.8rem 1.6rem !important; }
[data-testid="stVerticalBlock"] { gap:.65rem; }
.ps-header { background:var(--surface); border:1px solid var(--border);
  border-radius:10px; padding:15px 19px; margin:0; }
.ps-topbar,.ps-brand,.ps-chips { display:flex; align-items:center; gap:12px; flex-wrap:wrap; }
.ps-topbar { justify-content:space-between; }
.ps-mark { display:grid; place-items:center; width:42px; height:42px;
  border-radius:10px; background:var(--tint); color:var(--accent); font-size:24px; font-weight:750; }
.ps-brand h1 { font-size:21px; letter-spacing:-.035em; font-weight:730; margin:0; color:var(--text); }
.ps-brand h1 span { color:var(--muted); font-weight:500; }
.ps-chip { font-size:11px; color:var(--muted); border:1px solid var(--border); border-radius:6px; padding:5px 8px; }
.ps-chip.safe { background:var(--tint); color:var(--accent); }
.ps-sub { margin:5px 0 0 54px; color:var(--muted); font-size:12px; }
[data-testid="stWidgetLabel"] label,[data-testid="stWidgetLabel"] p,
[data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p { color:var(--muted) !important; font-size:12px; }
.stMarkdown h1,.stMarkdown h2,.stMarkdown h3,.stMarkdown h4 { color:var(--text) !important; letter-spacing:-.02em; }
.stMarkdown h3 { font-size:21px !important; font-weight:700; }
.stMarkdown h4 { font-size:15px !important; font-weight:650; }
.stMarkdown p, [data-testid="stRadio"] label p,[data-testid="stCheckbox"] label p,[data-testid="stToggle"] label p { color:var(--text); }
[data-testid="stForm"], [data-testid="stMetric"], [data-testid="stExpander"],
[data-testid="stVerticalBlockBorderWrapper"] > div {
  background:var(--surface) !important; border:1px solid var(--border) !important; border-radius:10px !important;
}
[data-testid="stForm"] { padding:15px 18px !important; }
[data-testid="stMetric"] { padding:12px 15px; min-height:76px; }
[data-testid="stMetricLabel"] { color:var(--muted) !important; }
[data-testid="stMetricValue"] { color:var(--text) !important; font-size:25px !important; font-weight:650; font-variant-numeric:tabular-nums; }
[data-testid="stMetricValue"] > div { font-size:25px !important; }
[data-testid="stExpander"] summary { color:var(--text) !important; }
div[data-baseweb="input"],div[data-baseweb="select"] > div,div[data-baseweb="base-input"],div[data-baseweb="textarea"] {
  background:var(--surface2) !important; border-color:var(--border) !important; color:var(--text) !important; border-radius:7px !important;
}
[data-testid="stNumberInput"] input,[data-testid="stTextInput"] input,[data-testid="stTextArea"] textarea { color:var(--text) !important; }
.stButton button,[data-testid="stFormSubmitButton"] button,[data-testid="stPopover"] button,[data-testid="stDownloadButton"] button {
  background:var(--surface2) !important; color:var(--text) !important; border:1px solid var(--border) !important;
  border-radius:7px !important; font-weight:600; min-height:34px; padding:.35rem .75rem; box-shadow:none !important;
}
.stButton button[kind="primary"],[data-testid="stFormSubmitButton"] button[kind="primary"] {
  background:#25a77e !important; border-color:#25a77e !important; color:#071e17 !important;
}
.stButton button:hover,[data-testid="stFormSubmitButton"] button:hover,[data-testid="stPopover"] button:hover {
  border-color:var(--accent) !important;
}
button:disabled { opacity:.5; }
button:focus-visible,a:focus-visible { outline:2px solid var(--accent) !important; outline-offset:2px; }
[data-testid="stForm"] [role="radiogroup"] { gap:5px; flex-wrap:wrap; }
[data-testid="stForm"] [role="radiogroup"] > label {
  background:var(--surface2); border:1px solid var(--border); border-radius:7px;
  padding:5px 9px; margin:0 !important; min-height:32px;
}
[data-testid="stForm"] [role="radiogroup"] > label:has(input:checked) {
  background:var(--tint); border-color:var(--accent);
}
[data-testid="stForm"] [role="radiogroup"] > label:has(input:checked) p { color:var(--accent) !important; font-weight:650; }
.st-key-ccs_selective_page [role="radiogroup"] { gap:24px; border-bottom:1px solid var(--border); flex-wrap:wrap; }
.st-key-ccs_selective_page [role="radiogroup"] > label { padding:8px 0; margin:0; border-bottom:2px solid transparent; }
.st-key-ccs_selective_page [role="radiogroup"] > label:has(input:checked) { border-bottom-color:var(--accent); }
.st-key-ccs_selective_page [role="radiogroup"] > label:has(input:checked) p { color:var(--accent) !important; font-weight:650; }
.stTabs [data-baseweb="tab-list"] { gap:12px; border-bottom:1px solid var(--border); }
.stTabs [data-baseweb="tab"] { color:var(--muted) !important; font-weight:650; }
.stTabs [data-baseweb="tab"][aria-selected="true"] { color:var(--accent) !important; background:var(--tint) !important; }
.stTabs [data-baseweb="tab-highlight"] { background:var(--accent) !important; }
.stTabs [data-baseweb="tab-border"] { background:var(--border) !important; }
[data-testid="stDataFrame"] { background:var(--surface) !important; border:1px solid var(--border); border-radius:9px; }
[data-testid="stAlert"] { border-radius:8px; }
.stApp a { color:var(--blue); }
.ccs-screen-head,.ccs-market-strip { background:var(--surface); border:1px solid var(--border); border-radius:9px; padding:11px 15px; color:var(--muted); }
.ccs-screen-kicker,.ccs-screen-head .ccs-accent { color:var(--accent); font-weight:650; }
.ccs-screen-head h3,.ccs-market-strip b { color:var(--text); margin:3px 0; }
.ccs-screen-head p { color:var(--muted); margin:0; font-size:12px; }
.ccs-market-strip { display:flex; gap:9px; align-items:center; flex-wrap:wrap; font-size:12px; }
.ccs-strip-dot { color:var(--accent); }
@media(max-width:760px) {
 .block-container { padding:.9rem .8rem 1.2rem !important; }
 .ps-header { padding:12px; } .ps-brand h1 { font-size:19px; } .ps-sub { margin-left:0; }
 .st-key-ccs_selective_page [role="radiogroup"] { gap:12px; }
 [data-testid="stMetricValue"] > div { font-size:20px !important; }
}
"""


def apply_terminal_theme(dark: bool) -> None:
    palette = DARK if dark else LIGHT
    variables = ";".join(f"--{key}:{value}" for key, value in palette.items())
    # Only static trusted palette values and CSS are interpolated.
    st.markdown(f"<style>:root{{{variables};color-scheme:{'dark' if dark else 'light'}}}{CSS}</style>",
                unsafe_allow_html=True)
