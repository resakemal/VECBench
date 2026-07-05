-- VidEpiCal multi-annotator schema
-- Run this once against your Supabase / Neon Postgres instance.

CREATE TABLE IF NOT EXISTS annotators (
    id            SERIAL PRIMARY KEY,
    access_code   TEXT UNIQUE NOT NULL,
    display_name  TEXT NOT NULL
);

-- One row per clip-level (e.g. clip "0042" at level "L0", "L1", ... "L3").
-- Main-study scheme: L0 (=P0, base), L1 (=P5), L2 (=P7), L3 (=P9), selected
-- from the 10-point pilot continuum. video_url points at the file in your
-- cloud bucket (GCS). Per-clip VMAF is not stored — target quality is
-- assumed fixed per level (L0=90, L1=60, L2=40, L3=20); see
-- db.LEVEL_TARGET_VMAF.
--
-- Assignment model (see assign_round1.py / assign_round2.py): a clip's L0
-- and its L1-L3 are deliberately assigned to DIFFERENT annotators, so the
-- degraded-level annotator has never seen the clean footage (avoids
-- recognition-memory contamination of the degraded caption). L1-L3 are
-- still edited from the L0 text as a weakening-only diff, preserving the
-- nested-omission property the PIU analysis needs — the base text is just
-- authored by someone else.
CREATE TABLE IF NOT EXISTS clips (
    id             SERIAL PRIMARY KEY,
    clip_name      TEXT NOT NULL,
    level          TEXT NOT NULL,               -- 'L0'..'L3' (main study)
    stage          TEXT NOT NULL,               -- '1_defiguration' | '2_degradation'
    gt_caption     TEXT DEFAULT '',             -- ground-truth caption (Stage 1 only)
    video_url      TEXT NOT NULL,
    UNIQUE (clip_name, level)
);

-- Assigns a clip-level to an annotator.
-- is_overlap_subset: independently double/multiply-annotated for IAA
--   (round 1: multiple L0 authors for the same clip; round 2: multiple
--   annotators independently captioning the same degraded level).
-- is_primary_base: for stage='1_defiguration' rows only — on overlap
--   clips with multiple L0 authors, exactly one is marked primary; that's
--   the caption round 2 uses as the degradation base. Non-overlap L0s are
--   trivially primary (sole author). Irrelevant for stage='2_degradation'.
CREATE TABLE IF NOT EXISTS assignments (
    id                  SERIAL PRIMARY KEY,
    annotator_id        INTEGER NOT NULL REFERENCES annotators(id),
    clip_id             INTEGER NOT NULL REFERENCES clips(id),
    is_overlap_subset   BOOLEAN DEFAULT FALSE,
    is_primary_base     BOOLEAN DEFAULT TRUE,
    status              TEXT DEFAULT 'pending',   -- pending | in_progress | done
    UNIQUE (annotator_id, clip_id)
);

-- Hedge/claim counts are intentionally not stored here — they were only a
-- rough proxy; exact figures come from PIU extraction downstream.
CREATE TABLE IF NOT EXISTS annotations (
    id                 SERIAL PRIMARY KEY,
    assignment_id      INTEGER NOT NULL REFERENCES assignments(id) UNIQUE,
    annotator_id       INTEGER NOT NULL REFERENCES annotators(id),
    clip_id            INTEGER NOT NULL REFERENCES clips(id),
    human_caption      TEXT NOT NULL,
    submitted_at       TIMESTAMPTZ DEFAULT now(),
    updated_at         TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_assignments_annotator ON assignments(annotator_id, status);
CREATE INDEX IF NOT EXISTS idx_clips_name ON clips(clip_name);
