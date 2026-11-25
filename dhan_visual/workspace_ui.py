"""Local v1.0 controls. No broker network calls on settings or saved-data rendering."""
import json
import subprocess
from pathlib import Path
from datetime import datetime
import streamlit as st
import pandas as pd

from . import workspace_store as store
from .api_monitor import stats, record
from .ce_eod_remote import IST

FILTER_KEYS={"maximum":"selective_contract_choice","minimum":"selective_premium_choice",
             "expiry":"selective_expiry_choice","r1_filter":"selective_r1_choice"}


def load_workspace():
    try:
        return store.read()
    except (OSError,ValueError,TypeError,ArithmeticError):
        st.warning("Local workspace could not be read. Restore a valid backup in Settings; existing file was not overwritten.")
        return None


def save_change(fn):
    try:
        store.change(fn)
        return True
    except (OSError,ValueError,TypeError,ArithmeticError):
        st.warning("Local workspace save failed. Existing data was not overwritten.")
        return False


def initialize():
    if st.session_state.get("ccs_workspace_initialized"):
        return
    st.session_state["ccs_workspace_initialized"]=True
    data=load_workspace()
    if data and data["remember"]:
        prefs=data["preferences"]
        for k,widget in (("dark","ccs_dark_mode"),("autofit","ccs_autofit_all")):
            if k in prefs:
                st.session_state.setdefault(widget,prefs[k])


def remember_pref(key,value):
    data=load_workspace()
    if data and data["remember"]:
        save_change(lambda d:d["preferences"].update({key:value}))


def prepare_filters(expiries):
    data=load_workspace()
    if not data:
        return
    if not st.session_state.get("ccs_scan_preferences_initialized"):
        st.session_state["ccs_scan_preferences_initialized"]=True
        f=data["preferences"].get("filters")
        if data["remember"] and f and f["expiry"] in expiries:
            for k,widget in FILTER_KEYS.items():
                st.session_state.setdefault(widget,f[k])
    if data["presets"]:
        name=st.selectbox("Saved scan preset",list(data["presets"]),key="ccs_saved_scan_preset")
        if st.button("Use preset",key="ccs_use_scan_preset"):
            f=data["presets"][name]
            if f["expiry"] not in expiries:
                st.warning("Saved expiry is unavailable. Choose a current expiry manually.")
            else:
                for k,widget in FILTER_KEYS.items():
                    st.session_state[widget]=f[k]
    if data["snapshot"]:
        with st.expander("Restore last saved shortlist (historical)",expanded=False):
            snap=data["snapshot"]
            st.caption("Historical snapshot saved "+snap["saved_at"]+" · not refreshed or used as live scan input")
            st.caption(str(snap["filters"]))
            if st.button("Show saved shortlist",key="ccs_restore_shortlist"):
                st.session_state["ccs_show_saved_shortlist"]=True
                record("Local","cache_hit")
            if st.session_state.get("ccs_show_saved_shortlist"):
                frame=pd.DataFrame(snap["rows"])
                st.dataframe(frame,hide_index=True,use_container_width=True)
                st.download_button("Download saved shortlist CSV",frame.to_csv(index=False),"ccs-shortlist.csv",key="ccs_saved_shortlist_csv")


def applied_filters(snapshot):
    return {k:snapshot[k] for k in FILTER_KEYS}


def save_scan_controls(snapshot,frame):
    data=load_workspace()
    if not data:
        return
    f=applied_filters(snapshot)
    with st.expander("Save scan preset / shortlist",expanded=False):
        name=st.text_input("Preset name",max_chars=60,key="ccs_preset_name")
        if st.button("Save preset",key="ccs_save_preset",disabled=not name.strip()):
            if save_change(lambda d:d["presets"].update({name.strip():f})):
                st.success("Preset saved.")
        payload=dict(saved_at=datetime.now(IST).isoformat(timespec="seconds"),filters=f,
                     rows=json.loads(frame.to_json(orient="records")))
        if st.button("Save shortlist snapshot",key="ccs_save_shortlist"):
            if save_change(lambda d:d.update(snapshot=payload)):
                st.success("Historical shortlist saved.")
    if data["remember"]:
        signature=(snapshot.get("cash_at"),snapshot.get("options_at"),len(snapshot.get("levels",{})),str(f),len(frame))
        if st.session_state.get("ccs_saved_scan_signature")!=signature and snapshot.get("cash_at"):
            def apply(d):
                d["preferences"]["filters"]=f
                d["snapshot"]=payload
            if save_change(apply):
                st.session_state["ccs_saved_scan_signature"]=signature


def render_settings():
    st.markdown("### CCS Terminal v"+store.VERSION)
    data=load_workspace()
    if data is not None:
        remember=st.checkbox("Remember preferences and save latest shortlist automatically",value=data["remember"],key="ccs_remember_workspace")
        if remember!=data["remember"]:
            save_change(lambda d:d.update(remember=remember))
        st.caption("Local storage: "+str(store.location())+" · no credentials")
        st.download_button("Export workspace backup",json.dumps(data,ensure_ascii=False,indent=2),
                           file_name="ccs-workspace-backup.json",mime="application/json")
    upload=st.file_uploader("Restore workspace backup (JSON)",type=["json"],key="ccs_restore_workspace")
    if upload is not None:
        if st.button("Replace local workspace with backup",key="ccs_restore_confirm"):
            try:
                raw=upload.getvalue()
                if len(raw)>store.MAX_BYTES:
                    raise ValueError("Backup exceeds limit")
                store.write(json.loads(raw))
                st.success("Backup restored. Reopen app to apply preferences.")
            except (OSError,ValueError,TypeError,ArithmeticError):
                st.error("Backup rejected. Existing workspace unchanged.")
    st.markdown("#### API Control Centre")
    usage=stats()
    a,b,c,d=st.columns(4)
    a.metric("FYERS requests today",usage["counts"].get(("FYERS","request"),0))
    b.metric("Yahoo downloads today",usage["counts"].get(("Yahoo","request"),0))
    c.metric("FYERS last minute",usage["minute"].get("FYERS",0))
    d.metric("FYERS 429 responses",usage["counts"].get(("FYERS","rate_limit"),0))
    st.caption("Process-local counters since launch; Yahoo counts download batches, not internal HTTP calls. FYERS uses the existing shared 1.5-second request gate. Last FYERS success: "+usage["last"].get("FYERS","—"))
    st.markdown("#### Software update")
    st.caption("Updates use the existing Git repository. Close the app before running Update CCS Terminal; only clean, fast-forward updates are allowed.")
    if st.button("Check GitHub for update",key="ccs_check_update"):
        try:
            root=Path(__file__).resolve().parent.parent
            local=subprocess.run(["git","rev-parse","HEAD"],cwd=root,check=True,capture_output=True,text=True,timeout=8).stdout.strip()
            remote=subprocess.run(["git","ls-remote","--exit-code","origin","refs/heads/main"],cwd=root,check=True,capture_output=True,text=True,timeout=15).stdout.split()[0]
            st.info("Up to date." if local==remote else "Remote main differs. Close CCS and run Update_CCS.bat to update safely.")
        except (OSError,subprocess.SubprocessError,IndexError):
            st.warning("Unable to verify GitHub update. Existing installation unchanged.")
    st.caption("Desktop shortcuts: run Install_CCS_Shortcuts.bat once. Open CCS Terminal from Desktop; use Update CCS Terminal after closing it.")
