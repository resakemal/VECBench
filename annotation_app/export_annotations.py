"""
export_annotations.py — pull the `annotations` table back out into a CSV
compatible with compute_full_dimensions.py.

Columns:
    clip_name, level, stage, base_caption, human_caption
Appended (multi-annotator-specific — safe to ignore if your pipeline reads
by column name, which pandas.read_csv does by default):
    annotator_name, is_overlap_subset, submitted_at

base_caption is reconstructed at export time (it isn't stored on the
annotation row itself): for L0 rows it's the clip's GT caption; for L1-L3
rows it's the L0 annotation's human_caption for that same clip_name.

Note: hedge/claim counts and VMAF are intentionally not included — hedge/
claim counts are superseded by exact PIU extraction downstream, and VMAF is
assumed fixed per level (L0=90, L1=60, L2=40, L3=20) rather than measured
per clip.

Usage:
    export DATABASE_URL="postgresql://..."
    python export_annotations.py --out annotations.csv
"""

import argparse
import csv
import os

import psycopg2
import psycopg2.extras


FIELDNAMES = [
    "clip_name", "level", "stage", "base_caption", "human_caption",
    "annotator_name", "is_overlap_subset", "is_primary_base", "submitted_at",
]


def fetch_rows(conn):
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT
                c.clip_name, c.level, c.stage, c.gt_caption,
                an.human_caption, an.submitted_at,
                a.is_overlap_subset, a.is_primary_base,
                ann.display_name AS annotator_name
            FROM annotations an
            JOIN assignments a ON a.id = an.assignment_id
            JOIN clips c ON c.id = an.clip_id
            JOIN annotators ann ON ann.id = an.annotator_id
            ORDER BY c.clip_name, c.level
            """
        )
        return cur.fetchall()


def fetch_base_captions(conn):
    """clip_name -> canonical L0 human_caption (is_primary_base = TRUE),
    for reconstructing base_caption on degraded-level rows."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT c.clip_name, an.human_caption
            FROM annotations an
            JOIN assignments a ON a.id = an.assignment_id
            JOIN clips c ON c.id = an.clip_id
            WHERE c.stage = '1_defiguration' AND a.is_primary_base = TRUE
            """
        )
        return {r["clip_name"]: r["human_caption"] for r in cur.fetchall()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="annotations.csv")
    args = ap.parse_args()

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows = fetch_rows(conn)
    base_by_clip = fetch_base_captions(conn)

    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        for r in rows:
            is_base = r["stage"] == "1_defiguration"
            base_caption = r["gt_caption"] if is_base else base_by_clip.get(r["clip_name"], "")
            writer.writerow({
                "clip_name": r["clip_name"],
                "level": r["level"],
                "stage": r["stage"],
                "base_caption": base_caption or "",
                "human_caption": r["human_caption"],
                "annotator_name": r["annotator_name"],
                "is_overlap_subset": r["is_overlap_subset"],
                "is_primary_base": r["is_primary_base"],
                "submitted_at": r["submitted_at"],
            })

    print(f"Wrote {len(rows)} annotation row(s) to {args.out}")


if __name__ == "__main__":
    main()
