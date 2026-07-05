"""
assign_round1.py — Round 1: distribute L0 (de-figuration) tasks.

Assigns L0 clips to annotators:
  - Most clips: one annotator each (round-robin), marked the sole/primary
    L0 author.
  - An overlap subset: assigned to a small GROUP of annotators (not all —
    see --round1-overlap-group-size) independently, for L0 inter-annotator
    agreement. One is marked is_primary_base=True; that's the caption
    round 2 will use as the degradation base. The rest are IAA-only and
    never feed downstream.

The overlap group size is capped below n_annotators - 3, so at least 3
annotators remain untouched by a given clip's L0 and can still caption its
degraded levels fresh in round 2 (assign_round2.py) — if every annotator
saw a clip's L0, nobody would be left to caption it without contamination.

Run this first. assign_round2.py can be run immediately after — it only
needs to know who is SCHEDULED to see a clip's L0 (from this script's
assignments), not whether they've submitted it yet.

Usage:
    export DATABASE_URL="postgresql://..."
    python assign_round1.py --manifest clips_manifest.csv \
        --round1-overlap-frac 0.08 --round1-overlap-group-size 2
"""

import argparse
import csv
import os
import random

import psycopg2


def load_manifest(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def insert_l0_clips(cur, l0_rows):
    clip_ids = {}  # clip_name -> id
    for r in l0_rows:
        cur.execute(
            """
            INSERT INTO clips (clip_name, level, stage, gt_caption, video_url)
            VALUES (%s, 'L0', '1_defiguration', %s, %s)
            ON CONFLICT (clip_name, level) DO UPDATE SET video_url = EXCLUDED.video_url
            RETURNING id
            """,
            (r["clip_name"], r.get("gt_caption", ""), r["video_url"]),
        )
        clip_ids[r["clip_name"]] = cur.fetchone()[0]
    return clip_ids


def assign_round1(cur, l0_rows, clip_ids, annotator_ids,
                   overlap_frac, overlap_group_size, seed=42):
    clip_names = sorted({r["clip_name"] for r in l0_rows})
    rng = random.Random(seed)
    rng.shuffle(clip_names)

    annotator_list = list(annotator_ids.values())
    n_annotators = len(annotator_list)
    # Leave at least 3 annotators untouched by this clip's L0 for round 2
    # (needed to fill L1, L2, L3 with 3 distinct fresh annotators).
    max_safe_group_size = max(2, n_annotators - 3)
    effective_group_size = min(overlap_group_size, max_safe_group_size)
    if effective_group_size < overlap_group_size:
        print(f"  ! Capping round1-overlap-group-size to {effective_group_size} "
              f"(requested {overlap_group_size}) to leave 3 annotators free "
              f"for round 2 with {n_annotators} total annotators.")

    n_overlap = max(1, int(len(clip_names) * overlap_frac)) if overlap_frac > 0 else 0
    overlap_clips = clip_names[:n_overlap]
    primary_clips = clip_names[n_overlap:]

    def assign(clip_name, annotator_id, is_overlap, is_primary_base):
        cur.execute(
            """
            INSERT INTO assignments (annotator_id, clip_id, is_overlap_subset, is_primary_base)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (annotator_id, clip_id) DO NOTHING
            """,
            (annotator_id, clip_ids[clip_name], is_overlap, is_primary_base),
        )

    for i, clip_name in enumerate(primary_clips):
        annotator_id = annotator_list[i % n_annotators]
        assign(clip_name, annotator_id, is_overlap=False, is_primary_base=True)

    for clip_name in overlap_clips:
        group = rng.sample(annotator_list, effective_group_size)
        for j, annotator_id in enumerate(group):
            assign(clip_name, annotator_id, is_overlap=True, is_primary_base=(j == 0))

    return {
        "primary_clips": len(primary_clips),
        "overlap_clips": len(overlap_clips),
        "overlap_group_size": effective_group_size,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True,
                     help="Manifest CSV (only L0 rows are used; L1-L3 rows are ignored here)")
    ap.add_argument("--round1-overlap-frac", type=float, default=0.08)
    ap.add_argument("--round1-overlap-group-size", type=int, default=2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    database_url = os.environ["DATABASE_URL"]
    rows = load_manifest(args.manifest)
    l0_rows = [r for r in rows if r["level"].strip().upper() == "L0"]
    if not l0_rows:
        raise SystemExit("No L0 rows found in manifest — check the 'level' column.")

    # Edit this list: (display_name, access_code) for your 5 annotators.
    annotators = [
        ("Annotator A", "vec_alpha"),
        ("Annotator B", "vec_beta"),
        ("Annotator C", "vec_charlie"),
        ("Annotator D", "vec_delta"),
        ("Annotator E", "vec_echo"),
    ]

    conn = psycopg2.connect(database_url)
    conn.autocommit = True
    with conn.cursor() as cur:
        clip_ids = insert_l0_clips(cur, l0_rows)
        annotator_ids = get_or_create_annotators(cur, annotators)
        summary = assign_round1(
            cur, l0_rows, clip_ids, annotator_ids,
            args.round1_overlap_frac, args.round1_overlap_group_size, args.seed,
        )

    print("Round 1 (de-figuration) assignments created.")
    print(f"  Primary (single-annotator) L0 clips: {summary['primary_clips']}")
    print(f"  Overlap L0 clips: {summary['overlap_clips']} "
          f"(each independently captioned by {summary['overlap_group_size']} annotators)")
    print("\nAccess codes issued:")
    for name, code in annotators:
        print(f"  {name}: {code}")
    print("\nNext: python assign_round2.py --manifest " + args.manifest)


if __name__ == "__main__":
    main()
