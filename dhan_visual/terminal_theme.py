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
 /* Real buttons; do not depend on radio-label DOM or hide input controls. */
 .st-key-ccs_phone_nav {
   position:fixed !important; left:0; right:0; bottom:0; z-index:999;
   width:100% !important; max-width:100% !important; box-sizing:border-box;
   background:var(--surface); border-top:1px solid var(--border);
   padding:4px 4px calc(4px + env(safe-area-inset-bottom, 0px));
 }
 .st-key-ccs_phone_nav [data-testid="stHorizontalBlock"] {
   display:grid !important; grid-template-columns:repeat(5,minmax(0,1fr));
   gap:2px !important; width:100% !important; flex-wrap:nowrap !important;
 }
 .st-key-ccs_phone_nav [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
   width:100% !important; min-width:0 !important; flex:1 1 0 !important;
 }
 .st-key-ccs_phone_nav .stButton button {
   height:52px !important; min-height:52px !important; padding:2px !important;
   border:0 !important; background:transparent !important;
   flex-direction:column !important; gap:2px !important;
   font-size:10px !important; white-space:nowrap !important;
 }
 .st-key-ccs_phone_nav .stButton button * {
   white-space:nowrap !important; overflow-wrap:normal !important;
   word-break:normal !important; line-height:1.15 !important;
 }
 .st-key-ccs_phone_nav .stButton button p { font-size:10px !important; margin:0 !important; }
 .st-key-ccs_phone_nav .stButton button[kind="primary"] {
   background:var(--tint) !important; color:var(--accent) !important;
 }
 .block-container { padding-bottom:calc(76px + env(safe-area-inset-bottom, 0px)) !important; }
 .ccs-card-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:7px; }
 .ccs-card-grid > div { background:var(--surface2); border:1px solid var(--border); padding:8px; border-radius:8px; min-width:0; }
 .ccs-card-grid small { display:block; font-size:11px; color:var(--muted); }
 .ccs-card-grid strong { display:block; font-size:15px; color:var(--text); overflow-wrap:anywhere; }
 [data-testid="stHorizontalBlock"] { flex-wrap:wrap; gap:.55rem; }
 [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
   flex:1 1 100% !important; width:100% !important; min-width:0 !important;
 }
 [data-testid="stHorizontalBlock"]:has(> [data-testid="stColumn"] [data-testid="stMetric"]) > [data-testid="stColumn"] { flex:1 1 calc(50% - .55rem) !important; width:calc(50% - .55rem) !important; }
 .st-key-ccs_phone_header [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { flex:1 1 0 !important; width:auto !important; }
 .st-key-ccs_phone_header .stButton button { min-height:38px; }
 .ps-chips .ps-chip:not(.safe) { display:none; }
 .ps-sub { display:none; }
 [data-testid="stWidgetLabel"] p, [data-testid="stCaptionContainer"] p { font-size:12px; }
 [data-testid="stMetricValue"] > div { font-size:20px !important; }
 [data-testid="stMetric"] { min-height:58px; padding:8px 12px; }
 [data-testid="stForm"] { padding:10px !important; }
 .stButton button, [data-testid="stFormSubmitButton"] button,
 [data-testid="stLinkButton"] a, [data-testid="stDownloadButton"] button {
   min-height:44px; width:100%; white-space:normal; overflow-wrap:anywhere;
 }
 [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
 [data-testid="stTextArea"] textarea { font-size:16px !important; }
 [data-testid="stForm"] [role="radiogroup"] > label { min-height:44px; }
 [data-testid="stRadio"] [role="radiogroup"] { flex-wrap:wrap; }
 .stTabs [data-baseweb="tab-list"] { overflow-x:auto; }
 .stTabs [data-baseweb="tab"] { min-height:44px; white-space:nowrap; }
 [data-testid="stDataFrame"], [data-testid="stPlotlyChart"] { max-width:100%; min-width:0; }
 .ps-chips { gap:5px; } .ps-topbar { gap:6px; } .ps-sub { font-size:12px; }
 .ps-mark { width:34px; height:34px; font-size:20px; }
 .ccs-screen-head,.ccs-market-strip { padding:9px; overflow-wrap:anywhere; }
 [data-testid="stHeader"] { height:2.2rem; }
 [data-testid="stToolbar"] { display:none !important; }
 .block-container { padding-top:2.4rem !important; }
 .ps-header { padding:7px 9px !important; }
 .ps-topbar { flex-wrap:nowrap !important; gap:4px !important; }
 .ps-brand { gap:6px !important; flex-wrap:nowrap !important; min-width:0; }
 .ps-brand h1 { font-size:15px !important; padding:0 !important; line-height:1.2 !important; white-space:nowrap; }
 .ps-mark { width:26px !important; height:26px !important; flex:none; font-size:16px !important; }
 .ps-chips { flex-wrap:nowrap !important; }
 .ps-chip.safe { font-size:9px !important; padding:3px 4px !important; white-space:nowrap; }
 .st-key-ccs_phone_header [data-testid="stExpander"] summary { padding:5px 8px !important; }
}
"""


def apply_terminal_theme(dark: bool) -> None:
    palette = DARK if dark else LIGHT
    variables = ";".join(f"--{key}:{value}" for key, value in palette.items())
    # Only static trusted palette values and CSS are interpolated.
    st.markdown(f"<style>:root{{{variables};color-scheme:{'dark' if dark else 'light'}}}{CSS}</style>",
                unsafe_allow_html=True)
