"""
db.py — Postgres access layer for the VidEpiCal multi-annotator web app.

Expects a DATABASE_URL in Streamlit secrets (.streamlit/secrets.toml):

    DATABASE_URL = "postgresql://user:password@host:port/dbname"

Works with any hosted Postgres (Supabase, Neon, etc.) since it's plain
psycopg2 — no vendor-specific client needed.
"""

from contextlib import contextmanager

import psycopg2
import psycopg2.extras
import streamlit as st

HEDGE_PHRASES = [
    "appears to be",
    "possibly",
    "what looks like",
    "it's unclear whether",
    "hard to tell, but maybe",
]

# Per-clip VMAF isn't measured/stored — quality target is assumed fixed per
# level. Purely informational for the annotator UI.
LEVEL_TARGET_VMAF = {
    "L0": 90,
    "L1": 60,
    "L2": 40,
    "L3": 20,
}


def is_base_level(level: str) -> bool:
    return level.upper() in ("L0", "P0")


@st.cache_resource
def _get_conn():
    # cache_resource keeps one connection alive across reruns/sessions
    # within this process. Streamlit reruns the script on every
    # interaction, so a plain psycopg2.connect() per call would be wasteful.
    conn = psycopg2.connect(st.secrets["DATABASE_URL"])
    conn.autocommit = True
    return conn


@contextmanager
def _cursor():
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        yield cur
    finally:
        cur.close()


# ----------------------------------------------------------------------
# Auth
# ----------------------------------------------------------------------

def authenticate(access_code: str):
    """Returns the annotator row dict, or None if the code is invalid."""
    with _cursor() as cur:
        cur.execute(
            "SELECT id, access_code, display_name FROM annotators WHERE access_code = %s",
            (access_code.strip(),),
        )
        return cur.fetchone()


# ----------------------------------------------------------------------
# Queue
# ----------------------------------------------------------------------

def get_queue(annotator_id: int):
    """All assignments for this annotator, joined with clip info. Under the
    two-round assignment design (assign_round1.py / assign_round2.py), a
    given annotator is never assigned both a clip's L0 and its degraded
    levels, so the stage ordering below is mostly moot per-annotator —
    kept as a harmless tiebreak in case that ever changes."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT a.id AS assignment_id, a.status, a.is_overlap_subset,
                   c.id AS clip_id, c.clip_name, c.level, c.stage,
                   c.gt_caption, c.video_url
            FROM assignments a
            JOIN clips c ON c.id = a.clip_id
            WHERE a.annotator_id = %s
            ORDER BY c.clip_name,
                     (c.stage != '1_defiguration'),  -- base stage first
                     c.level
            """,
            (annotator_id,),
        )
        return cur.fetchall()


def get_base_caption(clip_name: str):
    """Look up the canonical L0 caption for a clip_name. On overlap clips
    with multiple independent L0 authors, only the one marked
    is_primary_base is used as the degradation base — the others exist
    purely for L0 inter-annotator agreement and never feed downstream.
    Returns None if the primary L0 hasn't been submitted yet, even if a
    secondary overlap L0 has (keeps the base deterministic rather than
    racing on whichever submits first)."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT an.human_caption
            FROM annotations an
            JOIN assignments a ON a.id = an.assignment_id
            JOIN clips c ON c.id = an.clip_id
            WHERE c.clip_name = %s AND c.stage = '1_defiguration'
              AND a.is_primary_base = TRUE
            LIMIT 1
            """,
            (clip_name,),
        )
        row = cur.fetchone()
        return row["human_caption"] if row else None


def get_existing_annotation(assignment_id: int):
    with _cursor() as cur:
        cur.execute(
            "SELECT human_caption FROM annotations WHERE assignment_id = %s",
            (assignment_id,),
        )
        row = cur.fetchone()
        return row["human_caption"] if row else None


# ----------------------------------------------------------------------
# Saving
# ----------------------------------------------------------------------

def save_annotation(assignment_id, annotator_id, clip_id, caption):
    with _cursor() as cur:
        cur.execute(
            """
            INSERT INTO annotations
                (assignment_id, annotator_id, clip_id, human_caption, updated_at)
            VALUES (%s, %s, %s, %s, now())
            ON CONFLICT (assignment_id) DO UPDATE SET
                human_caption = EXCLUDED.human_caption,
                updated_at    = now()
            """,
            (assignment_id, annotator_id, clip_id, caption),
        )
        cur.execute(
            "UPDATE assignments SET status = 'done' WHERE id = %s",
            (assignment_id,),
        )


def mark_in_progress(assignment_id):
    with _cursor() as cur:
        cur.execute(
            "UPDATE assignments SET status = 'in_progress' "
            "WHERE id = %s AND status = 'pending'",
            (assignment_id,),
        )


# ----------------------------------------------------------------------
# Progress (for you, not the annotators)
# ----------------------------------------------------------------------

def get_progress_summary():
    with _cursor() as cur:
        cur.execute(
            """
            SELECT an.display_name,
                   COUNT(*) FILTER (WHERE a.status = 'done')        AS done,
                   COUNT(*) FILTER (WHERE a.status = 'in_progress') AS in_progress,
                   COUNT(*) FILTER (WHERE a.status = 'pending')     AS pending,
                   COUNT(*)                                         AS total
            FROM assignments a
            JOIN annotators an ON an.id = a.annotator_id
            GROUP BY an.display_name
            ORDER BY an.display_name
            """
        )
        return cur.fetchall()


def get_progress_by_stage():
    """Same as get_progress_summary but broken out by stage, so you can see
    round 1 (L0/de-figuration) vs round 2 (L1-L3/degradation) progress
    separately — useful for deciding when round 2 has enough L0s ready."""
    with _cursor() as cur:
        cur.execute(
            """
            SELECT an.display_name, c.stage,
                   COUNT(*) FILTER (WHERE a.status = 'done')        AS done,
                   COUNT(*) FILTER (WHERE a.status = 'in_progress') AS in_progress,
                   COUNT(*) FILTER (WHERE a.status = 'pending')     AS pending,
                   COUNT(*)                                         AS total
            FROM assignments a
            JOIN annotators an ON an.id = a.annotator_id
            JOIN clips c ON c.id = a.clip_id
            GROUP BY an.display_name, c.stage
            ORDER BY an.display_name, c.stage
            """
        )
        return cur.fetchall()
