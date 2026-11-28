"""Approved terminal presentation: no data, broker or scanner dependencies."""
import streamlit as st

DARK = {
    "page": "#101012", "surface": "#1c1c1e", "surface2": "#28282c",
    "border": "#353538", "text": "#f5f5f7", "muted": "#aaaab1",
    "accent": "#e5e5ea", "tint": "#30333b", "blue": "#90bbf2",
    "settings-muted": "#b8b8bf", "atm-bg": "#232d3b", "green": "#51bc8b", "red": "#ff777b",
}
LIGHT = {
    "page": "#f2f2f7", "surface": "#ffffff", "surface2": "#f2f2f7",
    "border": "#e5e5ea", "text": "#1c1c1e", "muted": "#777780",
    "accent": "#414854", "tint": "#e5e7ed", "blue": "#3769a6",
    "settings-muted": "#62626a", "atm-bg": "#edf2f8", "green": "#27835c", "red": "#d45656",
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
.ccs-card-symbol { font-weight:700; font-size:18px; color:var(--text); }
.ccs-card-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(130px,1fr)); gap:8px; }
.ccs-card-grid > div { min-width:0; background:var(--surface2); border:1px solid var(--border); padding:10px; border-radius:8px; }
.ccs-card-grid small { display:block; color:var(--muted); font-size:12px; }
.ccs-card-grid strong { display:block; color:var(--text); font-size:17px; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.ccs-card-grid .ccs-premium { color:var(--accent); }

/* Final approved iOS-inspired presentation. Scoped widget selectors only. */
.ccs-ios-title { font:760 25px/1.25 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 letter-spacing:-1px; color:var(--text) !important; visibility:visible !important; opacity:1 !important; }
.ccs-ios-summary { background:linear-gradient(130deg,#092b23,#174f3d 65%,#267251);
 color:#fff; padding:19px; border-radius:21px; overflow:hidden; }
.ccs-ios-summary small { color:#b2dfca; font-size:11px; }
.ccs-ios-count { font-size:39px; font-weight:700; letter-spacing:-2px; margin:7px 0; color:#fff; }
.ccs-ios-count span { font-size:17px; letter-spacing:0; }
.ccs-ios-progress { height:5px; border-radius:10px; background:#ffffff25; margin:12px 0 6px; overflow:hidden; }
.ccs-ios-progress i { display:block; height:100%; background:#67e5ae; }
.ccs-ios-summarygrid { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin-top:17px; padding-top:13px; border-top:1px solid #ffffff29; }
.ccs-ios-summarygrid strong { display:block; color:white; font-size:20px; margin-top:4px; }
.ccs-ios-stockhead { display:flex; gap:10px; align-items:center; color:var(--text); }
.ccs-ios-stockhead b { display:block; font-size:15px; color:var(--text); }
.ccs-ios-stockhead small { display:block; font-size:11px; color:var(--muted); }
.ccs-ios-stockhead strong { margin-left:auto; font-size:18px; white-space:nowrap; color:var(--text); font-variant-numeric:tabular-nums; }
.ccs-ios-logo { flex:none; width:38px; height:38px; display:grid; place-items:center; border-radius:12px; background:#e5e8ee; color:#465161; font-weight:750; }
.ccs-ios-foot { display:flex; justify-content:space-between; color:var(--muted); font-size:11px; }
.ccs-ios-tile-label { color:var(--muted); font-size:10px; font-weight:650; letter-spacing:.25px; }
[class*="st-key-ccs_ios_card_"] { border-radius:19px !important; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { width:100% !important; min-width:0 !important; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button { background:var(--surface2) !important; color:var(--accent) !important; border-radius:11px !important; min-height:49px; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button p { font-size:23px !important; font-weight:700; color:var(--accent) !important; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stColumn"]:nth-child(2) .ccs-ios-tile-label { color:var(--blue); }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stColumn"]:nth-child(2) [data-testid="stPopover"] button { background:var(--atm-bg) !important; color:var(--blue) !important; }
[class*="st-key-ccs_ios_tiles_"] [data-testid="stColumn"]:nth-child(2) [data-testid="stPopover"] button p { color:var(--blue) !important; }
.st-key-ccs_ios_dock { position:fixed !important; z-index:1000; bottom:0; left:0; right:0; padding:8px 4px max(12px,env(safe-area-inset-bottom)); background:var(--surface); border-top:1px solid var(--border); }
.st-key-ccs_ios_dock [data-testid="stHorizontalBlock"] { display:grid !important; grid-template-columns:repeat(5,minmax(0,1fr)); gap:0 !important; }
.st-key-ccs_ios_dock [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width:0 !important; width:100% !important; }
.st-key-ccs_ios_dock button { min-height:52px !important; padding:4px 0 !important; border:0 !important; border-radius:9px !important; background:transparent !important; color:var(--muted) !important; }
.st-key-ccs_ios_dock button[kind="primary"] { background:var(--tint) !important; color:var(--text) !important; }
.st-key-ccs_ios_dock button > div { flex-direction:column !important; gap:2px !important; }
.st-key-ccs_ios_dock button p { font-size:10px !important; white-space:nowrap !important; overflow-wrap:normal !important; color:inherit !important; }


/* Phone correction from Android screenshot: do not let the fixed Streamlit header
   cover the title; use the actual native button layout in the dock. */
.ccs-ios-status { color:var(--muted); font-size:12px; display:flex; align-items:center; gap:6px; }
.ccs-ios-dot { display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--muted); }
.ccs-ios-dot.ready { background:var(--green); }
.ccs-ios-empty { text-align:center; color:var(--muted); padding:24px 12px; font-size:13px; }
.ccs-ios-saved-tiles { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; margin-top:12px; }
.ccs-ios-saved-tiles > div { padding:11px; border-radius:11px; background:var(--surface2); color:var(--accent); }
.ccs-ios-saved-tiles > div:nth-child(2) { background:var(--atm-bg); color:var(--blue); }
.ccs-ios-saved-tiles small { display:block; font-size:10px; color:inherit; }
.ccs-ios-saved-tiles strong { display:block; font-size:23px; color:inherit; }
.st-key-ccs_ios_connect { border-radius:19px !important; }
.st-key-ccs_ios_connect button { background:var(--accent) !important; color:var(--surface) !important; border-radius:12px !important; min-height:44px; }
.st-key-ccs_ios_connect button p { color:var(--surface) !important; }


/* Settings only: reset native Streamlit/BaseWeb surfaces that may still follow
   the browser's theme rather than the CCS appearance toggle. */
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel) {
  color:var(--text);
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stCaptionContainer"],[data-testid="stCaptionContainer"] p,
    [data-testid="stWidgetLabel"] p) {
  color:var(--settings-muted) !important;
  opacity:1 !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stAlert"],[data-testid="stAlertContainer"],
    [data-testid="stAlert"] [role="alert"],[data-testid="stAlert"] [data-baseweb="notification"]) {
  background:var(--surface2) !important;
  color:var(--text) !important;
  border-radius:12px !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
[data-testid="stAlert"] :is(p,span,li,strong,div) {
  color:var(--text) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-baseweb="input"],[data-baseweb="base-input"],[data-baseweb="textarea"],
    [data-baseweb="select"] > div,[data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,[data-testid="stTextArea"] textarea) {
  background:var(--surface) !important;
  color:var(--text) !important;
  border-color:var(--border) !important;
  caret-color:var(--text) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stTextInput"],[data-testid="stNumberInput"]) button {
  background:var(--surface) !important; color:var(--text) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stTextInput"],[data-testid="stNumberInput"]) button svg {
  color:var(--text) !important; fill:currentColor;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is(input,textarea)::placeholder { color:var(--settings-muted) !important; opacity:1; }
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
.stMarkdown code {
  background:var(--surface2) !important; color:var(--text) !important;
  border-radius:5px; overflow-wrap:anywhere;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
[data-testid="stCheckbox"] label > div:first-child {
  background:var(--surface) !important;
  border-color:var(--settings-muted) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
[data-testid="stCheckbox"] label:has(input:checked) > div:first-child {
  background:var(--blue) !important; border-color:var(--blue) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stCheckbox"],[data-testid="stRadio"]) label p {
  color:var(--text) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stButton"],[data-testid="stFormSubmitButton"]) button[kind="primary"] {
  background:var(--accent) !important; border-color:var(--accent) !important;
  color:var(--surface) !important;
}
:is(.st-key-ccs_settings_panel,.st-key-ccs_legacy_settings_panel)
:is([data-testid="stButton"],[data-testid="stFormSubmitButton"]) button[kind="primary"] p {
  color:var(--surface) !important;
}

/* Mobile result area only. The approved summary, header and dock are unchanged. */



/* Home-only spacing correction. Keep the shared palette, dock and data flow. */



/* Keep the live Home summary and its native actions in normal document flow. */



/* Shared responsive layout contract for every phone workspace.
   Only the dock is fixed. All other elements reserve their normal flow space. */
.block-container:has(.st-key-ccs_ios_dock) {
  padding-bottom:calc(112px + env(safe-area-inset-bottom, 0px)) !important;
}
.st-key-ccs_ios_dock {
  box-sizing:border-box; width:100% !important; max-width:none !important;
  padding:8px 4px calc(10px + env(safe-area-inset-bottom, 0px)) !important;
}




/* Single mobile layout contract; no competing breakpoint overrides. */
@media(max-width:760px) {
 html, body, .stApp { -webkit-text-size-adjust:100%; text-size-adjust:100%; }
 .block-container {
   box-sizing:border-box; width:100% !important; min-width:0;
   max-width:480px !important; margin-inline:auto !important;
   padding:3rem 14px calc(120px + env(safe-area-inset-bottom, 0px)) !important;
 }
 [data-testid="stHeader"] { background:transparent !important; height:2.25rem !important; }
 [data-testid="stToolbar"] { display:none !important; }
 .block-container [data-testid="stVerticalBlock"] { gap:12px !important; min-width:0; }
 .block-container :is([data-testid="stElementContainer"],[data-testid="stMarkdownContainer"],
                      [data-testid="stMarkdown"],[data-testid="stHtml"],
                      [data-testid="stColumn"],[data-testid="column"],
                      [data-testid="stForm"],[data-testid="stExpander"]) {
   box-sizing:border-box; min-width:0 !important; max-width:100% !important;
 }
 .block-container :is([data-testid="stMarkdownContainer"],[data-testid="stHtml"]) {
   line-height:1.45; overflow-wrap:anywhere;
 }
 .block-container .stMarkdown :is(h1,h2,h3,h4) { line-height:1.3 !important; padding:0 !important; margin:0 0 4px !important; }
 .block-container .stMarkdown h3 { font-size:21px !important; }
 .block-container .stMarkdown h4 { font-size:17px !important; }
 .block-container [data-testid="stHorizontalBlock"] {
   display:flex !important; flex-direction:column !important; flex-wrap:nowrap !important;
   gap:10px !important; align-items:stretch !important; min-width:0;
 }
 .block-container [data-testid="stHorizontalBlock"] > :is([data-testid="stColumn"],[data-testid="column"]) {
   flex:0 0 auto !important; width:100% !important; min-width:0 !important;
 }
 .block-container :is([class*="st-key-ccs_ios_tiles_"],.st-key-ccs_card_actions)
 [data-testid="stHorizontalBlock"] {
   display:grid !important; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px !important;
 }
 .ccs-ios-title { display:block; font-size:26px; line-height:1.25; padding:0; margin:0; }
 .ccs-ios-status { font-size:13px; line-height:1.5; padding:3px 0 10px; margin:0; color:var(--settings-muted); }
 .ccs-ios-dot { flex:0 0 7px; }
 .ccs-ios-summary { box-sizing:border-box; display:block; position:relative; margin:0; padding:16px 18px; height:auto; border-radius:18px; }
 .ccs-ios-summary small { display:block; line-height:1.45; font-size:12px; }
 .ccs-ios-count { display:flex; align-items:baseline; flex-wrap:wrap; gap:8px; font-size:34px; line-height:1.15; margin:10px 0; }
 .ccs-ios-count span { font-size:17px; line-height:1.35; }
 .ccs-ios-progress { margin:14px 0 8px; height:7px; }
 .ccs-ios-refresh-status,.ccs-ios-refresh-elapsed { color:#cce9dc !important; overflow-wrap:anywhere; }
 .ccs-ios-refresh-elapsed { margin-top:4px; }
 .ccs-ios-summarygrid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; margin-top:14px; padding-top:12px; }
 .ccs-ios-summarygrid > div,.ccs-ios-saved-tiles > div { min-width:0; }
 .ccs-ios-summarygrid strong { font-size:21px; line-height:1.3; overflow-wrap:anywhere; }
 .ccs-ios-empty { padding:14px 8px; line-height:1.5; }
 .ccs-ios-stockhead { display:grid; grid-template-columns:36px minmax(0,1fr) auto; align-items:center; gap:9px; }
 .ccs-ios-stockhead > div { min-width:0; }
 .ccs-ios-logo { width:36px; height:36px; }
 .ccs-ios-stockhead b { font-size:16px; line-height:1.3; overflow-wrap:anywhere; }
 .ccs-ios-stockhead small { font-size:12px; line-height:1.4; overflow-wrap:anywhere; }
 .ccs-ios-stockhead strong { font-size:15px; margin:0; line-height:1.35; white-space:normal; overflow-wrap:anywhere; }
 .ccs-ios-foot { font-size:12px; line-height:1.5; gap:6px; flex-wrap:wrap; }
 .ccs-ios-saved-tiles { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .ccs-ios-saved-tiles strong { font-size:21px; line-height:1.3; overflow-wrap:anywhere; }
 .ccs-card-grid { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .ccs-card-grid small { line-height:1.4; overflow-wrap:anywhere; }
 .ccs-card-grid strong { font-size:16px; line-height:1.35; }
 :is([class*="st-key-ccs_ios_card_"],[class*="st-key-ccs_ios_saved_card_"],.st-key-ccs_ios_connect) {
   box-sizing:border-box; background:var(--surface) !important; border:1px solid var(--border) !important;
   border-radius:16px !important; padding:12px !important; margin:0 !important;
 }
 :is([class*="st-key-ccs_ios_card_"],[class*="st-key-ccs_ios_saved_card_"],.st-key-ccs_ios_connect)
 [data-testid="stVerticalBlock"] { gap:9px !important; }
 [class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button {
   min-height:62px !important; padding:8px 4px !important; height:auto !important;
 }
 [class*="st-key-ccs_ios_tiles_"] [data-testid="stPopover"] button p {
   font-size:15px !important; line-height:1.4 !important; white-space:normal !important;
   overflow-wrap:anywhere;
 }
 .block-container :is([data-testid="stWidgetLabel"],[data-testid="stCaptionContainer"]) p {
   font-size:13px !important; line-height:1.45 !important; opacity:1 !important;
   color:var(--settings-muted) !important; overflow-wrap:anywhere;
 }
 .block-container :is([data-testid="stForm"],[data-testid="stMetric"]) { padding:12px !important; }
 .block-container [data-testid="stMetric"] { min-height:0; }
 .block-container [data-testid="stMetricValue"] > div { font-size:20px !important; white-space:normal; overflow-wrap:anywhere; }
 .block-container [data-testid="stExpander"] summary { min-height:44px; height:auto; padding:8px 12px; }
 .block-container [data-testid="stExpander"] summary p { line-height:1.4; white-space:normal; overflow-wrap:anywhere; }
 .block-container :is([data-testid="stButton"],[data-testid="stFormSubmitButton"],[data-testid="stDownloadButton"],
                      [data-testid="stLinkButton"],[data-testid="stPopover"]) :is(button,a) {
   box-sizing:border-box; width:100%; max-width:100%; min-width:0; min-height:44px; height:auto;
   white-space:normal; overflow-wrap:anywhere; padding:8px 10px;
 }
 .block-container :is([data-testid="stButton"],[data-testid="stFormSubmitButton"],[data-testid="stDownloadButton"],
                      [data-testid="stLinkButton"],[data-testid="stPopover"]) :is(button,a) p {
   font-size:14px; line-height:1.4; white-space:normal; overflow-wrap:anywhere;
 }
 .block-container :is([data-testid="stTextInput"],[data-testid="stNumberInput"],
                      [data-testid="stTextArea"]) :is(input,textarea) { font-size:16px !important; }
 .block-container :is([data-testid="stPlotlyChart"],[data-testid="stDataFrame"],[data-testid="stSelectbox"],
                      [data-testid="stMultiSelect"],[data-testid="stSlider"],[data-testid="stFileUploader"],
                      [data-testid="stTabs"],[data-testid="stCode"]) { min-width:0; max-width:100%; width:100%; }
 .block-container [data-testid="stText"] pre { white-space:pre-wrap; overflow-wrap:anywhere; }
 .block-container [data-baseweb="tab-list"] { max-width:100%; overflow-x:auto; }
 .block-container [data-baseweb="tab"] { min-height:44px; white-space:nowrap; }
 .block-container [role="radiogroup"] { flex-wrap:wrap; gap:8px; }
 .block-container [role="radiogroup"] > label { min-height:44px; min-width:0; max-width:100%; margin:0 !important; }
 .block-container [role="radiogroup"] > label p { white-space:normal; overflow-wrap:anywhere; }
 .block-container [data-testid="stAlert"] :is(p,span,li,strong) { color:var(--text) !important; }
 .block-container [data-testid="stAlert"] [data-baseweb="notification"] { background:var(--surface2) !important; }
 .st-key-ccs_refresh_ready { background:var(--surface) !important; border-color:var(--green) !important; }
 .st-key-ccs_refresh_ready button[kind="primary"] { background:var(--green) !important; color:var(--surface) !important; }
 .st-key-ccs_refresh_ready button[kind="primary"] p { color:var(--surface) !important; }
 .block-container .st-key-ccs_ios_dock {
   position:fixed !important; box-sizing:border-box; width:100% !important; max-width:none !important;
   left:0; right:0; bottom:0; z-index:1000;
   padding:6px 5px calc(8px + env(safe-area-inset-bottom, 0px)) !important;
 }
 .block-container .st-key-ccs_ios_dock [data-testid="stHorizontalBlock"] {
   display:grid !important; grid-template-columns:repeat(5,minmax(0,1fr)); gap:2px !important;
 }
 .block-container .st-key-ccs_ios_dock button {
   width:100% !important; height:auto !important; min-height:52px !important;
   padding:4px 1px !important; display:flex !important; flex-direction:column !important;
   align-items:center !important; justify-content:center !important; gap:3px !important;
 }
 .block-container .st-key-ccs_ios_dock button > :is(div,span) {
   flex-direction:column !important; align-items:center !important; gap:3px !important;
 }
 .block-container .st-key-ccs_ios_dock button p {
   font-size:clamp(9px,2.6vw,11px) !important; line-height:1.2 !important;
   white-space:normal !important; text-align:center;
 }
 .block-container .st-key-ccs_ios_dock button [data-testid="stIconMaterial"] { font-size:21px !important; }
}


/* CCS appearance is authoritative for native controls, including portal menus.
   Pair foreground and background on the actual inner surfaces, not just wrappers. */
[data-testid="stExpander"] details,
[data-testid="stExpander"] summary,
[data-testid="stExpanderDetails"],
[data-testid="stExpander"] details > div,
[data-testid="stPopoverBody"],
[data-testid="stDialog"] [role="dialog"],
[data-baseweb="popover"] > div,
[data-baseweb="menu"],
[role="listbox"],
[data-testid="stFileUploaderDropzone"] {
  background:var(--surface) !important;
  color:var(--text) !important;
  border-color:var(--border) !important;
}
[data-testid="stExpander"] summary :is(p,span,svg),
[data-testid="stExpanderDetails"] > div,
[data-testid="stPopoverBody"] :is(h1,h2,h3,h4,label),
[data-testid="stDialog"] :is(h1,h2,h3,h4,label),
[data-testid="stFileUploaderDropzone"] :is(p,small,span) {
  color:var(--text) !important;
}
:is([data-testid="stExpanderDetails"],[data-testid="stPopoverBody"],[data-testid="stDialog"])
 [data-testid="stMarkdownContainer"] :is(p,li,strong,em) {
  color:var(--text) !important;
}
:is([data-testid="stExpanderDetails"],[data-testid="stPopoverBody"],[data-testid="stDialog"])
 [data-testid="stCaptionContainer"] :is(p,span) { color:var(--settings-muted) !important; }
[data-testid="stText"],[data-testid="stText"] pre,
[data-testid="stText"] p { color:var(--text) !important; }
.stApp :is([data-testid="stTextInput"],[data-testid="stNumberInput"],[data-testid="stTextArea"]) :is(input,textarea),
.stApp [data-baseweb="select"] :is(input,[role="combobox"]) {
  background:transparent !important; color:var(--text) !important;
  caret-color:var(--text) !important; -webkit-text-fill-color:var(--text);
}
.stApp :is(input,textarea)::placeholder { color:var(--settings-muted) !important; opacity:1; -webkit-text-fill-color:var(--settings-muted); }
[data-baseweb="select"] > div,
[data-baseweb="select"] [data-baseweb="tag"],
[data-testid="stNumberInput"] button,
[data-testid="stTextInput"] button {
  background:var(--surface2) !important; color:var(--text) !important;
  border-color:var(--border) !important;
}
[data-baseweb="select"] :is(span,svg),[data-baseweb="tag"] :is(span,svg),
[data-testid="stNumberInput"] button svg,[data-testid="stTextInput"] button svg {
  color:var(--text) !important;
}
[role="option"],[data-baseweb="menu"] li {
  background:var(--surface) !important; color:var(--text) !important;
}
[role="option"] :is(div,span,p),[data-baseweb="menu"] li :is(div,span,p) {
  color:var(--text) !important;
}
[role="option"]:hover,[role="option"][aria-selected="true"],
[data-baseweb="menu"] li:hover,[role="option"]:focus-visible {
  background:var(--tint) !important; color:var(--text) !important;
}
.stApp :is([data-testid="stRadio"],[data-testid="stCheckbox"],[data-testid="stToggle"]) label p {
  color:var(--text) !important;
}
.stApp [data-testid="stCaptionContainer"] p { color:var(--settings-muted) !important; opacity:1; }
.stApp :is([data-testid="stAlert"],[data-testid="stAlertContainer"],[data-testid="stAlert"] [role="alert"]) {
  background:var(--surface2) !important; color:var(--text) !important;
}
.stApp [data-testid="stAlert"] :is(p,li,strong,span) { color:var(--text) !important; }
.stApp [data-testid="stMarkdownContainer"] a { color:var(--blue) !important; }
.stApp [data-testid="stLinkButton"] a {
  background:var(--surface2) !important; color:var(--text) !important; border-color:var(--border) !important;
}
.stApp :is([data-testid="stButton"],[data-testid="stFormSubmitButton"],[data-testid="stDownloadButton"],[data-testid="stLinkButton"],[data-testid="stPopover"])
 :is(button,a) :is(p,span) { color:inherit !important; }
.stApp [data-testid="stMarkdownContainer"] :is(table,th,td) {
  background:var(--surface) !important; color:var(--text) !important; border-color:var(--border) !important;
}
.stApp [data-testid="stMarkdownContainer"] :is(code,pre),
.stApp [data-testid="stCode"] pre {
  background:var(--surface2) !important; color:var(--text) !important;
}
.stApp [data-testid="stMetricLabel"] p { color:var(--settings-muted) !important; }
.stApp [data-testid="stMetricValue"] { color:var(--text) !important; }
.stApp .ccs-ios-logo { background:var(--tint); color:var(--accent); }
/* Preserve the intentionally green hero, and the two distinct premium tiles. */
.stApp .ccs-ios-summary { color:#fff !important; }
.stApp .ccs-ios-summary small { color:#cce9dc !important; }
.stApp [class*="st-key-ccs_ios_tiles_"] [data-testid="stColumn"]:nth-child(2) [data-testid="stPopover"] button { color:var(--blue) !important; }


/* The stock summary is the only disclosure hit target: no extra button,
   marker, box or reserved row. Native details preserves scroll on toggle. */
.ccs-card-disclosure { width:100%; min-width:0; }
.ccs-card-disclosure > summary {
  display:block; list-style:none; cursor:pointer; border-radius:10px;
  padding:2px; min-width:0; -webkit-tap-highlight-color:transparent;
}
.ccs-card-disclosure > summary::-webkit-details-marker { display:none; }
.ccs-card-disclosure > summary::marker { content:""; }
.ccs-card-disclosure > summary:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.ccs-card-disclosure > summary:active { background:var(--surface2); }
.ccs-card-disclosure > summary > .ccs-ios-saved-tiles { margin:8px 0; }
.ccs-card-disclosure > summary > .ccs-card-grid { margin-top:8px; }
.ccs-card-detail-body {
  margin-top:10px; border-top:1px solid var(--border); padding-top:9px;
  display:grid; gap:7px; color:var(--text); min-width:0; font-size:13px;
}
.ccs-card-detail-body > div { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1fr); gap:8px; }
.ccs-card-detail-body > div > span { color:var(--settings-muted); overflow-wrap:anywhere; }
.ccs-card-detail-body > div > strong { text-align:right; color:var(--text); overflow-wrap:anywhere; }
.ccs-card-detail-body > p { margin:0; color:var(--settings-muted); }
.ccs-card-detail-body > a { color:var(--blue); padding:5px 0; }
/* Native Streamlit strategy actions remain server-validated and are visible
   only while their own card is open. Hide their layout wrapper as well. */
:is([class*="st-key-ccs_ios_card_"],[class*="st-key-ccs_ios_saved_card_"]):not(:has(.ccs-card-disclosure[open]))
  :is([class*="st-key-ccs_card_details_actions_"],
      [data-testid="stLayoutWrapper"]:has([class*="st-key-ccs_card_details_actions_"]):not(:has(.ccs-card-disclosure))) {
  display:none !important;
}


/* Explicit in-flow spacing for Android Home; do not rely on native Markdown
   wrapper gaps, which vary between Streamlit versions and WebViews. */
.ccs-ios-summary-wrap { display:flow-root; min-width:0; padding:4px 0 10px; }
.ccs-ios-summary-meta {
  display:grid; gap:4px; min-width:0; padding:10px 2px 2px;
  color:var(--settings-muted); font-size:12px; line-height:1.5;
  overflow-wrap:anywhere;
}
.ccs-ios-summary-meta > div { min-width:0; }
.ccs-ios-summary-meta .ccs-ios-saved-stamp { font-weight:600; color:var(--text); }
.ccs-ios-status { color:var(--settings-muted); }
@media(max-width:760px) {
  .block-container:has(.ccs-ios-summary) { padding-top:2.25rem !important; }
  .ccs-ios-summary-wrap { padding-top:5px; padding-bottom:12px; }
  .ccs-ios-summary-meta { padding-top:12px; font-size:12px; line-height:1.55; }
  .ccs-ios-summary-wrap .ccs-ios-summarygrid { margin-top:12px; padding-top:10px; }
  .st-key-ccs_mobile_results .stMarkdown h3 { margin-bottom:4px !important; }
}

"""


def apply_terminal_theme(dark: bool) -> None:
    palette = DARK if dark else LIGHT
    variables = ";".join(f"--{key}:{value}" for key, value in palette.items())
    # Only static trusted palette values and CSS are interpolated.
    st.html(f"<style>:root{{{variables};color-scheme:{'dark' if dark else 'light'}}}{CSS}</style>")
