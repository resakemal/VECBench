"""
app.py — VidEpiCal multi-annotator web app (Streamlit).

Deploy on Streamlit Community Cloud:
  1. Push this folder to a GitHub repo.
  2. In Streamlit Cloud, set app.py as the entry point.
  3. Add DATABASE_URL to the app's Secrets (Settings > Secrets).
  4. Give each annotator the app URL + their access code.

Local run:
  streamlit run app.py
(requires a .streamlit/secrets.toml with DATABASE_URL set locally too)
"""

import difflib

import streamlit as st

import db
import examples

st.set_page_config(page_title="VidEpiCal Annotator", layout="wide")

HEDGE_PHRASES = db.HEDGE_PHRASES

STAGE1_HINT = (
    "**Stage 1 — De-figuration (authoring the L0 base)**\n\n"
    "The GT caption on the left is a reference, not a starting point — write "
    "your own caption from scratch, keeping only grounded, verifiable "
    "visual claims.\n"
    "- **Leave out** anything false or unverifiable — claims the GT caption "
    "makes that aren't actually visible, or figurative/interpretive language.\n"
    "- **Include** anything true and visible that the GT caption missed.\n\n"
    "Granularity floor: describe each thing as specifically as you can "
    "**confidently verify** — object type, colour, count, readable text, coarse "
    "position — but no finer than that. This becomes L0."
)

STAGE2_HINT = (
    "**Stage 2 — Degradation (editing the L0 base to match this video)**\n\n"
    "This L0 base caption was written by a different annotator, from the "
    "clean video — you're seeing this clip for the first time at this "
    "degraded quality, which is intentional (it keeps your judgment about "
    "what's still visible free of memory from a clean viewing).\n\n"
    "You may only **weaken** existing base claims — never add new ones. For "
    "each claim that no longer fully holds, apply the first move that fits:\n"
    "1. **Reduce specificity** — coarser but still confidently true "
    "(\"red hatchback\" → \"red car\" → \"car\" → \"vehicle\").\n"
    "2. **Hedge** — same claim, lowered confidence, using only the standardized "
    "phrases below.\n"
    "3. **Remove** — when nothing about the entity is verifiable any more.\n\n"
    f"Standardized hedge phrases (use these only): {', '.join(f'`{p}`' for p in HEDGE_PHRASES)}"
)

NO_BASE_HINT = (
    "⚠️ This clip's L0 base caption hasn't been written yet by its primary "
    "annotator. Skip this clip for now and come back once L0 is done."
)

PROJECT_DESCRIPTION = """
### About this project

**VidEpiCal** studies whether AI video-description models adjust what they
claim to see as video quality gets worse — the way a careful human would
say "hard to tell, but possibly a red car" instead of confidently naming
the make and model of something too blurry to make out. Your captions
become the human reference this project compares AI-generated descriptions
against, so accuracy and honesty about what's actually visible matter more
than writing style or length.

**What you'll be doing** falls into two kinds of tasks:

- **De-figuration (L0)** — given a clean, full-quality video and an
  existing description, you'll edit it down to only claims you can
  personally verify from the video, removing anything false or overly
  figurative and adding anything true it missed.
- **Degradation (L1-L3)** — given a *degraded* video and someone else's
  L0 description of it, you'll edit that description down to only what's
  still verifiable at the lower quality — reducing specificity, hedging,
  or removing claims that no longer hold, never adding new ones.

You'll sometimes be asked to caption a degraded video whose clean version
you've never seen — that's intentional, not an error. It keeps your
judgment about what's visible limited to what's actually in the degraded
footage, rather than filled in from memory of a clearer version you saw
earlier.
"""


def render_diff(base: str, current: str):
    if not base or not current:
        return
    base_words, cur_words = base.split(), current.split()
    matcher = difflib.SequenceMatcher(a=base_words, b=cur_words, autojunk=False)
    parts = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            parts.append(" ".join(base_words[i1:i2]))
        elif tag == "delete":
            parts.append(f":red[~~{' '.join(base_words[i1:i2])}~~]")
        elif tag == "insert":
            parts.append(f":blue[**{' '.join(cur_words[j1:j2])}**]")
        elif tag == "replace":
            parts.append(f":red[~~{' '.join(base_words[i1:i2])}~~]")
            parts.append(f":blue[**{' '.join(cur_words[j1:j2])}**]")
    st.markdown(" ".join(parts))


def login_screen():
    st.title("📹 VidEpiCal Annotator")
    st.markdown(PROJECT_DESCRIPTION)
    code = st.text_input("Access code", type="password")
    if st.button("Log in") and code:
        annotator = db.authenticate(code)
        if annotator:
            st.session_state["annotator"] = annotator
            st.rerun()
        else:
            st.error("Access code not recognized. Check with the project owner.")


def apply_hedge_to_text(current_text: str, phrase: str) -> str:
    # Streamlit text areas don't expose cursor/selection, so we append the
    # hedge phrase at the end for the annotator to reposition manually —
    # simpler and less error-prone than trying to fake selection handling.
    return (current_text.rstrip() + " " + phrase + " ").strip() + " "


def section_select_screen(annotator):
    """Landing screen after login: pick de-figuration or degradation.
    Degradation is locked until the annotator has finished all their own
    L0 (de-figuration) tasks."""
    st.title("📹 VidEpiCal Annotator")
    st.sidebar.write(f"Logged in as **{annotator['display_name']}**")
    if st.sidebar.button("Log out"):
        for k in ("annotator", "section", "idx"):
            st.session_state.pop(k, None)
        st.rerun()

    remaining_l0 = db.count_incomplete_defiguration(annotator["id"])

    st.subheader("Choose a section")
    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### 1 · De-figuration (L0)")
        st.caption("Write grounded base captions from clean, full-quality videos.")
        if st.button("Start de-figuration", type="primary", use_container_width=True):
            st.session_state["section"] = "1_defiguration"
            st.session_state["idx"] = 0
            st.rerun()

    with col2:
        st.markdown("### 2 · Degradation (L1-L3)")
        st.caption("Weaken existing base captions to match degraded videos.")
        if remaining_l0 > 0:
            st.button("🔒 Locked", disabled=True, use_container_width=True)
            st.caption(
                f"Finish your {remaining_l0} remaining de-figuration "
                f"task(s) first — the degraded clips build on those base captions."
            )
        else:
            if st.button("Start degradation", use_container_width=True):
                st.session_state["section"] = "2_degradation"
                st.session_state["idx"] = 0
                st.rerun()
            blocked = db.count_degradation_blocked_on_base(annotator["id"])
            if blocked > 0:
                st.caption(
                    f"⏳ {blocked} of your degradation clip(s) are waiting on "
                    f"their L0 base caption, which another annotator hasn't "
                    f"submitted yet. You can start the rest now and come back "
                    f"to these later — they'll unlock automatically as those "
                    f"L0 captions come in."
                )


def render_defiguration_example():
    st.info(
        "**Example — de-figuration.** Below is a worked example: the original "
        "caption (full of figurative/interpretive language and some errors) and "
        "a good de-figured L0 result that keeps only grounded, verifiable claims. "
        "Study it, then scroll down to your own clips."
    )
    url = examples.EXAMPLE_VIDEO_URLS.get("L0", "")
    if url:
        col_l, col_c, col_r = st.columns([1, 2, 1])
        with col_c:
            st.video(url)
    ex_l, ex_r = st.columns(2)
    with ex_l:
        st.text_area("Original caption (before)", examples.DEFIGURATION_EXAMPLE_GT,
                      height=300, disabled=True, key="ex_defig_gt")
    with ex_r:
        st.text_area("Good L0 result (after)", examples.DEFIGURATION_EXAMPLE_L0,
                      height=300, disabled=True, key="ex_defig_l0")
    st.divider()


def render_degradation_example():
    st.info(
        "**Example — degradation.** Below is a worked example showing how the "
        "same L0 base caption is weakened as quality drops: claims lose "
        "specificity (\"fox\" → \"possibly a fox or a cat\"), gain hedges "
        "(\"possibly\", \"seemingly\"), or are removed when unreadable "
        "(\"SHINee Includes...\" → \"unreadable text\"). Study it, then scroll "
        "down to your own clips."
    )
    cols = st.columns(4)
    example_levels = [
        ("L0 base", examples.EXAMPLE_VIDEO_URLS.get("L0", ""), examples.DEFIGURATION_EXAMPLE_L0),
        ("L1 (blurry)", examples.EXAMPLE_VIDEO_URLS.get("L1", ""), examples.DEGRADATION_EXAMPLE_L1),
        ("L2 (very blurry)", examples.EXAMPLE_VIDEO_URLS.get("L2", ""), examples.DEGRADATION_EXAMPLE_L2),
        ("L3 (extremely blurry)", examples.EXAMPLE_VIDEO_URLS.get("L3", ""), examples.DEGRADATION_EXAMPLE_L3),
    ]
    for col, (label, url, caption) in zip(cols, example_levels):
        with col:
            st.markdown(f"**{label}**")
            if url:
                st.video(url)
            st.text_area(f"{label} caption", caption, height=340,
                          disabled=True, key=f"ex_degrade_{label}")
    st.divider()


def annotation_screen():
    annotator = st.session_state["annotator"]
    section = st.session_state["section"]
    section_label = "De-figuration (L0)" if section == "1_defiguration" else "Degradation (L1-L3)"

    st.sidebar.write(f"Logged in as **{annotator['display_name']}**")
    st.sidebar.write(f"Section: **{section_label}**")
    if st.sidebar.button("← Back to sections"):
        st.session_state.pop("section", None)
        st.session_state["idx"] = 0
        st.rerun()
    if st.sidebar.button("Log out"):
        for k in ("annotator", "section", "idx"):
            st.session_state.pop(k, None)
        st.rerun()

    # Show the worked example every time the annotator is in a section, as a
    # reminder — even if they've already annotated some of their own clips.
    if section == "1_defiguration":
        render_defiguration_example()
    else:
        render_degradation_example()

    full_queue = db.get_queue(annotator["id"])
    if not full_queue:
        st.info("No clips assigned to you yet — check back once assignments are set up.")
        return

    # Restrict to the chosen section only.
    queue = [q for q in full_queue if q["stage"] == section]
    if not queue:
        st.info(f"No {section_label} clips assigned to you.")
        return

    pending = [q for q in queue if q["status"] != "done"]
    done_count = len(queue) - len(pending)
    st.sidebar.progress(done_count / len(queue) if queue else 0)
    st.sidebar.write(f"{done_count} / {len(queue)} done in this section")

    if not pending:
        if section == "1_defiguration":
            st.success("🎉 You've finished all your de-figuration clips! "
                        "The degradation section is now unlocked from the "
                        "section menu.")
        else:
            st.success("🎉 You've completed all your assigned clips. Thank you!")
        return

    # In the degradation section, surface how many clips are still waiting on
    # another annotator's L0 base — and handle the case where ALL remaining
    # clips are blocked, so the annotator isn't left clicking "skip" blindly.
    if section == "2_degradation":
        blocked = db.count_degradation_blocked_on_base(annotator["id"])
        if blocked >= len(pending):
            st.info(
                f"⏳ All {len(pending)} of your remaining degradation clip(s) "
                f"are waiting on their L0 base caption from another annotator. "
                f"There's nothing to do here right now — please check back "
                f"later, they'll unlock automatically as those L0 captions "
                f"are submitted."
            )
            return
        elif blocked > 0:
            st.warning(
                f"⏳ {blocked} of your remaining degradation clip(s) are "
                f"waiting on their L0 base from another annotator and will be "
                f"skipped for now — you can finish the rest and return to them "
                f"later."
            )

    if "idx" not in st.session_state or st.session_state["idx"] >= len(pending):
        st.session_state["idx"] = 0

    item = pending[st.session_state["idx"]]
    db.mark_in_progress(item["assignment_id"])

    is_base = db.is_base_level(item["level"])
    # overlap_tag = " · overlap/IAA clip" if item["is_overlap_subset"] else ""
    st.caption(
        # f"Clip **{item['clip_name']}** — level **{item['level']}**"
        # f"{overlap_tag} — {st.session_state['idx']+1} of {len(pending)} remaining"
        f"Clip **{item['clip_name']}**"
        f"{st.session_state['idx']+1} of {len(pending)} remaining"
    )

    # col_video, col_hint = st.columns([1, 1.2])

    # with col_video:
    #     st.video(item["video_url"])
    #     target = db.LEVEL_TARGET_VMAF.get(item["level"])
    #     if target is not None:
    #         st.caption(f"Nominal quality target: VMAF ~{target}")
    st.video(item["video_url"])

    # with col_hint:
    #     if is_base:
    #         st.markdown(STAGE1_HINT)
    #         base_caption = item["gt_caption"] or ""
    #         reference_label = "GT caption (reference)"
    #     else:
    #         st.markdown(STAGE2_HINT)
    #         base_caption = db.get_base_caption(item["clip_name"]) or ""
    #         reference_label = "L0 base caption (read-only)"
    if is_base:
        st.markdown(STAGE1_HINT)
        base_caption = item["gt_caption"] or ""
        reference_label = "GT caption (reference)"
    else:
        st.markdown(STAGE2_HINT)
        base_caption = db.get_base_caption(item["clip_name"]) or ""
        reference_label = "L0 base caption (read-only)"

    # For degraded levels with no base yet, bail out before the editor.
    if not is_base and not base_caption:
        st.warning(NO_BASE_HINT)
        if st.button("Skip for now →"):
            st.session_state["idx"] += 1
            st.rerun()
        return

    existing = db.get_existing_annotation(item["assignment_id"])
    if existing:
        default_text = existing
    elif is_base:
        # Stage 1: start blank. The GT caption stays visible in the
        # read-only reference box (col_ref) for the annotator to consult,
        # but isn't copied into the editable box — this is a write-your-own
        # task, not an edit-in-place task.
        default_text = ""
    else:
        # Stage 2: start as an editable copy of the L0 base, since this
        # task IS edit-in-place (weaken-only diff against L0).
        default_text = base_caption

    text_key = f"caption_{item['assignment_id']}"
    if text_key not in st.session_state:
        st.session_state[text_key] = default_text

    if not is_base:
        st.write("Insert hedge phrase:")
        hcols = st.columns(len(HEDGE_PHRASES))
        for hc, phrase in zip(hcols, HEDGE_PHRASES):
            if hc.button(phrase, key=f"hedge_{item['assignment_id']}_{phrase}"):
                st.session_state[text_key] = apply_hedge_to_text(
                    st.session_state[text_key], phrase
                )

    # Reference (left) and editable caption (right) side-by-side at full
    # page width, both tall enough to read a full ARGUS-length caption
    # without scrolling.
    CAPTION_HEIGHT = 360
    col_ref, col_yours = st.columns(2)
    with col_ref:
        st.text_area(reference_label, base_caption,
                      height=CAPTION_HEIGHT, disabled=True)
    with col_yours:
        caption = st.text_area("Your caption", key=text_key,
                                height=CAPTION_HEIGHT)

    if not is_base:
        render_diff(base_caption, caption)

    b1, b2, b3 = st.columns(3)
    with b1:
        if st.button("⏮ Previous", disabled=st.session_state["idx"] == 0):
            st.session_state["idx"] -= 1
            st.rerun()
    with b2:
        if st.button("Skip →"):
            st.session_state["idx"] += 1
            st.rerun()
    with b3:
        if st.button("💾 Save & Next", type="primary"):
            if not caption.strip():
                st.warning(
                    "Empty caption. If the clip is too degraded to describe, "
                    "write: \"Unable to determine content due to severe degradation.\""
                )
            else:
                db.save_annotation(
                    item["assignment_id"], annotator["id"], item["clip_id"],
                    caption.strip(),
                )
                st.session_state["idx"] += 1
                st.rerun()


def main():
    if "annotator" not in st.session_state:
        login_screen()
    elif "section" not in st.session_state:
        section_select_screen(st.session_state["annotator"])
    else:
        annotation_screen()


if __name__ == "__main__":
    main()