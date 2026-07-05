"""
admin_dashboard.py — progress overview for you (not the annotators).

Run separately:
    streamlit run admin_dashboard.py
Protect it with its own secret if you deploy it publicly (e.g. a
simple password gate), since it shows all annotators' progress.
"""

import streamlit as st

import db

st.set_page_config(page_title="VidEpiCal — Admin", layout="wide")

ADMIN_PASSWORD = st.secrets.get("ADMIN_PASSWORD", "")

st.title("📊 VidEpiCal Annotation Progress")

if ADMIN_PASSWORD:
    pw = st.text_input("Admin password", type="password")
    if pw != ADMIN_PASSWORD:
        st.stop()

summary = db.get_progress_summary()
if not summary:
    st.info("No assignments yet.")
else:
    st.subheader("Overall")
    for row in summary:
        pct = row["done"] / row["total"] if row["total"] else 0
        st.write(f"**{row['display_name']}** — {row['done']} / {row['total']} done "
                  f"({row['in_progress']} in progress, {row['pending']} pending)")
        st.progress(pct)

    st.subheader("By round")
    st.caption("1_defiguration = round 1 (L0) · 2_degradation = round 2 (L1-L3)")
    by_stage = db.get_progress_by_stage()
    for row in by_stage:
        pct = row["done"] / row["total"] if row["total"] else 0
        st.write(f"**{row['display_name']}** — {row['stage']} — "
                  f"{row['done']} / {row['total']} done")
        st.progress(pct)
