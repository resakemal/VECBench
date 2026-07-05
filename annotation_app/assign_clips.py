"""
assign_clips.py — one-off script to populate `clips` and `assignments`.

DEPRECATED for multi-annotator use: this script keeps a clip's full L0-L3
chain with ONE annotator, which means the degraded-level captions are
written by someone who has already seen the clean video — a known
recognition-memory contamination risk. Use assign_round1.py (L0) +
assign_round2.py (L1-L3, assigned to annotators who did NOT see that
clip's L0) instead; they enforce "fresh eyes" on degraded levels while
still deriving them from the same L0 text as a weakening-only diff.

Kept here only for single-annotator or small-team use where that
contamination risk is acceptable or irrelevant (e.g. solo pilot work).

Run this locally (not in the Streamlit app) after uploading videos to your
bucket. It:
  1. Reads a manifest CSV describing every clip-level and its video URL.
  2. Inserts them into `clips`.
  3. Splits clip_names round-robin across annotators, keeping each clip's
     FULL level-chain (P0 + all degraded levels) with ONE primary annotator
     — degraded levels depend on that annotator's own P0 caption.
  4. Additionally assigns an overlap subset of clips to a second annotator
     independently (their own full chain, written from scratch) for
     inter-annotator agreement.

Usage:
    python assign_clips.py --manifest clips_manifest.csv --overlap-frac 0.08

clips_manifest.csv columns expected:
    clip_name, level, stage, gt_caption, video_url
(gt_caption only needs to be populated for stage == '1_defiguration' rows;
 per-clip VMAF is not tracked — quality target is assumed fixed per level:
 L0=90, L1=60, L2=40, L3=20.)
"""

import argparse
import csv
import os
import random

import psycopg2
import psycopg2.extras

# Main-study level scheme: 4 compound tiers selected from the 10-point pilot
# continuum. L0 is the de-figuration base (unchanged from P0); L1-L3 are the
# three degradation tiers actually annotated in the main study.
LEVEL_MAP = {
    "P0": "L0",
    "P5": "L1",
    "P7": "L2",
    "P9": "L3",
}


def load_manifest(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def remap_and_filter_levels(rows):
    """Keep only rows whose level maps to the main-study scheme (P0/P5/P7/P9,
    or already-relabeled L0-L3), and rewrite their level field to L0-L3.
    Rows for any other pilot-only level (P1-P4, P6, P8) are dropped — they
    were pilot-only and aren't part of the main-study annotation set."""
    kept = []
    dropped = 0
    for r in rows:
        level = r["level"].strip().upper()
        if level in LEVEL_MAP:
            r = dict(r)
            r["level"] = LEVEL_MAP[level]
            kept.append(r)
        elif level in LEVEL_MAP.values():
            kept.append(r)  # already relabeled
        else:
            dropped += 1
    if dropped:
        print(f"Dropped {dropped} manifest row(s) at pilot-only levels not in the main study.")
    return kept


def insert_clips(cur, rows):
    clip_ids = {}  # (clip_name, level) -> id
    for r in rows:
        cur.execute(
            """
            INSERT INTO clips (clip_name, level, stage, gt_caption, video_url)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (clip_name, level) DO UPDATE SET
                video_url = EXCLUDED.video_url
            RETURNING id
            """,
            (r["clip_name"], r["level"], r["stage"], r.get("gt_caption", ""), r["video_url"]),
        )
        clip_ids[(r["clip_name"], r["level"])] = cur.fetchone()[0]
    return clip_ids


def get_or_create_annotators(cur, names_and_codes):
    """names_and_codes: list of (display_name, access_code) tuples."""
    ids = {}
    for name, code in names_and_codes:
        cur.execute(
            """
            INSERT INTO annotators (access_code, display_name)
            VALUES (%s, %s)
            ON CONFLICT (access_code) DO UPDATE SET display_name = EXCLUDED.display_name
            RETURNING id
            """,
            (code, name),
        )
        ids[name] = cur.fetchone()[0]
    return ids


def build_assignments(cur, rows, clip_ids, annotator_ids, overlap_frac, seed=42):
    clip_names = sorted({r["clip_name"] for r in rows})
    rng = random.Random(seed)
    rng.shuffle(clip_names)

    annotator_list = list(annotator_ids.values())
    n_annotators = len(annotator_list)

    n_overlap = max(1, int(len(clip_names) * overlap_frac))
    overlap_clips = set(clip_names[:n_overlap])
    primary_clips = clip_names[n_overlap:]

    rows_by_clip = {}
    for r in rows:
        rows_by_clip.setdefault(r["clip_name"], []).append(r)

    def assign_full_chain(clip_name, annotator_id, is_overlap):
        for r in rows_by_clip[clip_name]:
            cid = clip_ids[(clip_name, r["level"])]
            cur.execute(
                """
                INSERT INTO assignments (annotator_id, clip_id, is_overlap_subset)
                VALUES (%s, %s, %s)
                ON CONFLICT (annotator_id, clip_id) DO NOTHING
                """,
                (annotator_id, cid, is_overlap),
            )

    # Primary: round-robin, one annotator owns the whole chain per clip
    for i, clip_name in enumerate(primary_clips):
        annotator_id = annotator_list[i % n_annotators]
        assign_full_chain(clip_name, annotator_id, is_overlap=False)

    # Overlap: every annotator independently annotates the same overlap clips
    for clip_name in overlap_clips:
        for annotator_id in annotator_list:
            assign_full_chain(clip_name, annotator_id, is_overlap=True)

    return {
        "primary_clips": len(primary_clips),
        "overlap_clips": len(overlap_clips),
        "annotators": n_annotators,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--overlap-frac", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    database_url = os.environ["DATABASE_URL"]  # export before running
    rows = load_manifest(args.manifest)
    rows = remap_and_filter_levels(rows)
    if not rows:
        raise SystemExit("No rows left after level filtering — check your manifest's 'level' column.")

    # Edit this list: (display_name, access_code) for your 5 annotators.
    annotators = [
        ("Annotator A", "CHANGE-ME-A"),
        ("Annotator B", "CHANGE-ME-B"),
        ("Annotator C", "CHANGE-ME-C"),
        ("Annotator D", "CHANGE-ME-D"),
        ("Annotator E", "CHANGE-ME-E"),
    ]

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        clip_ids = insert_clips(cur, rows)
        annotator_ids = get_or_create_annotators(cur, annotators)
        summary = build_assignments(
            cur, rows, clip_ids, annotator_ids, args.overlap_frac, args.seed
        )

    print("Done.")
    print(f"  Primary (single-annotator) clips: {summary['primary_clips']}")
    print(f"  Overlap (all-annotator) clips:     {summary['overlap_clips']}")
    print(f"  Annotators:                        {summary['annotators']}")
    print("\nAccess codes issued:")
    for name, code in annotators:
        print(f"  {name}: {code}")


if __name__ == "__main__":
    main()
