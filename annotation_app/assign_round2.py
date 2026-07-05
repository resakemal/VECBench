"""
assign_round2.py — Round 2: distribute L1-L3 (degradation) tasks.

For each clip with L1-L3 videos, assigns each of L1/L2/L3 to a DIFFERENT
annotator, drawn only from annotators who were NOT assigned that clip's L0
in round 1 — in ANY role, primary or overlap. This is what keeps degraded
captions free of "I already know what's in the clean video" contamination
(recognition-memory bias): the annotator captioning a degraded level has
never seen that clip's clean footage.

Can be run immediately after assign_round1.py — it only needs to know who
is SCHEDULED to see a clip's L0 (from the assignments table), not whether
they've submitted it. Individual annotators simply can't start a degraded
clip in the app until its primary L0 caption is actually submitted (the
app shows a "come back later" message and a skip button in that case).

Two independent overlap mechanisms exist for inter-annotator agreement:
  - Round 1 (assign_round1.py) covers L0 agreement.
  - This script's --round2-overlap-frac covers degradation-captioning
    agreement: for that fraction of eligible clips, EVERY remaining fresh
    annotator captions EVERY degraded level independently (rather than one
    annotator per level), so you can measure agreement on the
    degradation-captioning task itself, holding the base text fixed.

Clips with fewer than 3 annotators left eligible (e.g. a round-1 overlap
clip whose L0 group size ate too far into the pool) are skipped and
reported — widen the annotator pool or lower --round1-overlap-group-size
if this happens a lot.

Usage:
    export DATABASE_URL="postgresql://..."
    python assign_round2.py --manifest clips_manifest.csv \
        --round2-overlap-frac 0.08
"""

import argparse
import csv
import os
import random

import psycopg2
import psycopg2.extras


def load_manifest(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def insert_degraded_clips(cur, degraded_rows):
    clip_ids = {}  # (clip_name, level) -> id
    for r in degraded_rows:
        cur.execute(
            """
            INSERT INTO clips (clip_name, level, stage, gt_caption, video_url)
            VALUES (%s, %s, '2_degradation', '', %s)
            ON CONFLICT (clip_name, level) DO UPDATE SET video_url = EXCLUDED.video_url
            RETURNING id
            """,
            (r["clip_name"], r["level"], r["video_url"]),
        )
        clip_ids[(r["clip_name"], r["level"])] = cur.fetchone()["id"]
    return clip_ids


def get_l0_authors(cur):
    """clip_name -> set of annotator_ids assigned that clip's L0 in round 1,
    regardless of primary/overlap role. ALL of them are excluded from
    captioning that clip's degraded levels."""
    cur.execute(
        """
        SELECT c.clip_name, a.annotator_id
        FROM assignments a
        JOIN clips c ON c.id = a.clip_id
        WHERE c.stage = '1_defiguration'
        """
    )
    authors = {}
    for row in cur.fetchall():
        authors.setdefault(row["clip_name"], set()).add(row["annotator_id"])
    return authors


def get_all_annotator_ids(cur):
    cur.execute("SELECT id FROM annotators ORDER BY id")
    return [r["id"] for r in cur.fetchall()]


def assign_round2(cur, degraded_rows, clip_ids, l0_authors, all_annotator_ids,
                   overlap_frac, seed=42):
    rng = random.Random(seed)
    by_clip = {}
    for r in degraded_rows:
        by_clip.setdefault(r["clip_name"], []).append(r)

    eligible_clips = []       # (clip_name, recs, eligible_annotator_ids)
    skipped_no_l0 = []
    skipped_too_few_eligible = []

    for clip_name, recs in by_clip.items():
        if clip_name not in l0_authors:
            skipped_no_l0.append(clip_name)
            continue
        excluded = l0_authors[clip_name]
        eligible = [a for a in all_annotator_ids if a not in excluded]
        if len(eligible) < 3:
            skipped_too_few_eligible.append(clip_name)
            continue
        eligible_clips.append((clip_name, recs, eligible))

    rng.shuffle(eligible_clips)
    n_overlap = max(1, int(len(eligible_clips) * overlap_frac)) if overlap_frac > 0 else 0
    overlap_set = {c for c, _, _ in eligible_clips[:n_overlap]}

    def assign(clip_name, level, annotator_id, is_overlap):
        cur.execute(
            """
            INSERT INTO assignments (annotator_id, clip_id, is_overlap_subset, is_primary_base)
            VALUES (%s, %s, %s, TRUE)
            ON CONFLICT (annotator_id, clip_id) DO NOTHING
            """,
            (annotator_id, clip_ids[(clip_name, level)], is_overlap),
        )

    for clip_name, recs, eligible in eligible_clips:
        is_overlap = clip_name in overlap_set
        levels_present = sorted({r["level"] for r in recs})
        if is_overlap:
            # Every eligible (fresh) annotator captions every degraded
            # level independently, using the same L0 base — measures
            # degradation-captioning agreement holding the base fixed.
            for level in levels_present:
                for annotator_id in eligible:
                    assign(clip_name, level, annotator_id, is_overlap=True)
        else:
            # One distinct annotator per level, drawn without replacement.
            pool = eligible[:]
            rng.shuffle(pool)
            for level, annotator_id in zip(levels_present, pool):
                assign(clip_name, level, annotator_id, is_overlap=False)

    return {
        "assigned_clips": len(eligible_clips),
        "overlap_clips": len(overlap_set),
        "skipped_no_l0": skipped_no_l0,
        "skipped_too_few_eligible": skipped_too_few_eligible,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True,
                     help="Manifest CSV (only L1-L3 rows are used here)")
    ap.add_argument("--round2-overlap-frac", type=float, default=0.08)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    database_url = os.environ["DATABASE_URL"]
    rows = load_manifest(args.manifest)
    degraded_rows = [r for r in rows if r["level"].strip().upper() in ("L1", "L2", "L3")]
    if not degraded_rows:
        raise SystemExit("No L1-L3 rows found in manifest — check the 'level' column.")

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        clip_ids = insert_degraded_clips(cur, degraded_rows)
        l0_authors = get_l0_authors(cur)
        all_annotator_ids = get_all_annotator_ids(cur)
        summary = assign_round2(
            cur, degraded_rows, clip_ids, l0_authors, all_annotator_ids,
            args.round2_overlap_frac, args.seed,
        )

    print("Round 2 (degradation) assignments created.")
    print(f"  Clips assigned: {summary['assigned_clips']}")
    print(f"  Overlap clips (every fresh annotator does every level): {summary['overlap_clips']}")
    if summary["skipped_no_l0"]:
        names = ", ".join(summary["skipped_no_l0"][:5])
        more = ", ..." if len(summary["skipped_no_l0"]) > 5 else ""
        print(f"  Skipped ({len(summary['skipped_no_l0'])}) — no L0 assignment found yet "
              f"(run assign_round1.py first, or check clip_name matches): {names}{more}")
    if summary["skipped_too_few_eligible"]:
        names = ", ".join(summary["skipped_too_few_eligible"][:5])
        more = ", ..." if len(summary["skipped_too_few_eligible"]) > 5 else ""
        print(f"  Skipped ({len(summary['skipped_too_few_eligible'])}) — fewer than 3 annotators "
              f"free of that clip's L0 (widen the annotator pool or lower "
              f"--round1-overlap-group-size): {names}{more}")


if __name__ == "__main__":
    main()
