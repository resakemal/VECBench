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

st.set_page_config(page_title="VidEpiCal Annotator", layout="wide")

HEDGE_PHRASES = db.HEDGE_PHRASES

STAGE1_HINT = (
    "**Stage 1 — De-figuration (authoring the L0 base)**\n\n"
    "Write a detailed caption describing the video with the ground truth (GT) caption"
    "as reference, keeping only grounded and verifiable visual claims.\n"
    "- **Leave out** anything false or unverifiable — claims the GT caption "
    "makes that aren't actually visible, or figurative/interpretive language"
    "(\"a sense of nostalgia\", \"appears well-loved\").\n"
    "- **Include** anything true and visible that the GT caption missed.\n\n"
    "Granularity floor: describe each thing as specifically as you can "
    "**confidently verify** — object type, colour, count, readable text, coarse "
    "position — but no finer than that. This becomes L0.\n\n"
    "**FIRST READ THE INSTRUCTIONS BELOW, THEN WATCH THE WHOLE VIDEO ONCE"
    "FROM START TO FINISH BEFORE YOU START WRITING**\n\n"
    "Three rules for what and how to describe:\n\n"
    "1. **Name at the everyday level.** Use the ordinary word for a thing "
    "(\"dog\", \"car\", \"bottle\"). Go *more* specific (\"golden retriever\", "
    "\"red hatchback\") only when you can point to the visual evidence for it; "
    "stay *more* general (\"animal\", \"vehicle\") when even the everyday word "
    "is uncertain.\n"
    "2. **Point-to-it test for each claim.** Include a claim only if you could "
    "pause the video and point to the pixels that justify it. If you can't, "
    "leave it out.\n"
    "3. **Foreground first.** Describe the entities and actions central to "
    "what's happening. Include background detail only if it's prominent or "
    "clearly intentional — don't exhaustively inventory everything.\n\n"
    "Don't aim for any particular length — say exactly as much as passes the "
    "three rules, no more and no less.\n\n"
    "Hint: You can copy-paste text from the GT caption and use it as a base;"
    "you need to select the text (still possible even with disabled cursor), "
    "and perform right click + click 'Copy' instead of Ctrl+C due to the latter"
    "being the Clear Cache command in Streamlit."
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

**VidEpiCal** studies whether video captioning of VLMs adjust what they
claim to see as video quality gets worse — the way a careful human would
say "hard to tell, but possibly a red car" instead of confidently naming
the make and model of something too blurry to make out. Your captions
become the human reference this project compares AI-generated descriptions
against, so accuracy and honesty about what's actually visible matter more
than writing style or length.

**Tasks**:

- **De-figuration (L0)** — given a clean, full-quality video and an
  existing description, you'll edit it down to only claims you can
  personally verify from the video, removing anything false or overly
  figurative (e.g. The classroom was *as quiet as a mouse.*)
  and adding anything true it missed.
- **Degradation (L1-L3)** — given a *degraded* video and a
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


def annotation_screen():
    annotator = st.session_state["annotator"]
    st.sidebar.write(f"Logged in as **{annotator['display_name']}**")
    if st.sidebar.button("Log out"):
        del st.session_state["annotator"]
        st.rerun()

    queue = db.get_queue(annotator["id"])
    if not queue:
        st.info("No clips assigned to you yet — check back once assignments are set up.")
        return

    pending = [q for q in queue if q["status"] != "done"]
    done_count = len(queue) - len(pending)
    st.sidebar.progress(done_count / len(queue) if queue else 0)
    st.sidebar.write(f"{done_count} / {len(queue)} done")

    if not pending:
        st.success("🎉 You've completed all your assigned clips. Thank you!")
        return

    if "idx" not in st.session_state or st.session_state["idx"] >= len(pending):
        st.session_state["idx"] = 0

    item = pending[st.session_state["idx"]]
    db.mark_in_progress(item["assignment_id"])

    is_base = db.is_base_level(item["level"])
    # overlap_tag = " · overlap/IAA clip" if item["is_overlap_subset"] else ""
    # st.caption(
    #     f"Clip **{item['clip_name']}** — level **{item['level']}**"
    #     f"{overlap_tag} — {st.session_state['idx']+1} of {len(pending)} remaining"
    # )
    st.caption(
        f"{st.session_state['idx']+1} of {len(pending)} remaining"
    )

    col_video, col_hint = st.columns([1, 1.2])

    with col_video:
        st.video(item["video_url"])
        # target = db.LEVEL_TARGET_VMAF.get(item["level"])
        # if target is not None:
        #     st.caption(f"Nominal quality target: VMAF ~{target}")

    with col_hint:
        if is_base:
            st.markdown(STAGE1_HINT)
            # base_caption = item["gt_caption"] or ""
            base_caption = ""
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
        # Keep empty on stage 1
        default_text = ""
    else:
        # Use L0 caption as base on stage 2
        default_text = base_caption

    text_key = f"caption_{item['assignment_id']}"
    if text_key not in st.session_state:
        st.session_state[text_key] = default_text

    # Buttons to apply hedge to text; disable due to limitation of only appending
    # and not on selection/caret
    # if not is_base:
    #     st.write("Insert hedge phrase:")
    #     hcols = st.columns(len(HEDGE_PHRASES))
    #     for hc, phrase in zip(hcols, HEDGE_PHRASES):
    #         if hc.button(phrase, key=f"hedge_{item['assignment_id']}_{phrase}"):
    #             st.session_state[text_key] = apply_hedge_to_text(
    #                 st.session_state[text_key], phrase
    #             )

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
    else:
        annotation_screen()


if __name__ == "__main__":
    main()