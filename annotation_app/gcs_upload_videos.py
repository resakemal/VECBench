"""
gcs_upload_videos.py — upload local clip files to a GCS bucket and fill in
the manifest's video_url column, so assign_clips.py can consume it unchanged.

Three ways to supply the manifest:

  A) Directly from your pipeline outputs:
       --clip-table  clip_table_cache.csv   (L0 base videos + GT captions;
                                              columns: clip_name, video_path,
                                              l0_vmaf, human_caption, ...)
       --manifest-json  manifest.json       (L1-L3 degraded records, already
                                              labeled L1/L2/L3 by
                                              video_degrade_v3.py --mode actual)
       --data-root   local directory the relative paths inside those two
                     files are resolved against (video_path / output fields
                     use Windows-style relative paths, e.g.
                     "argus_data\\videos\\X.mp4", "exp_1\\degraded\\L1\\X.mp4")

  B) A pre-built manifest CSV:
       --video-dir   directory of local videos named "{clip_name}_{level}.mp4"
       --manifest-in manifest CSV missing only the video_url column

  C) A folder with L0-L3 subfolders (recommended if that's how your videos
     are already organized):
       --video-root  a directory containing subfolders L0/, L1/, L2/, L3/,
                     each holding video files named "{clip_name}.mp4" (or
                     .mov/.mkv/.webm)
       --captions    a CSV with columns clip_name,human_caption — used as
                     the GT caption for L0 clips only (Stage 1 hasn't
                     de-figured anything yet at upload time)

Bucket setup (one-time, via gcloud or console):
    gcloud storage buckets create gs://YOUR_BUCKET --location=us-central1
    gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET \
        --member=allUsers --role=roles/storage.objectViewer
(The IAM binding makes objects publicly readable via their GCS URL. Skip
this and see the README's signed-URL note if you'd rather not make the
bucket public.)

Usage (mode A):
    python gcs_upload_videos.py \
        --clip-table clip_table_cache.csv \
        --manifest-json manifest.json \
        --data-root /path/to/your/project/data \
        --bucket your-bucket-name \
        --manifest-out clips_manifest.csv

Usage (mode B):
    python gcs_upload_videos.py \
        --video-dir ./clips \
        --manifest-in clips_manifest_no_urls.csv \
        --bucket your-bucket-name \
        --manifest-out clips_manifest.csv

Usage (mode C):
    python gcs_upload_videos.py \
        --video-root ./clips_by_level \
        --captions argus_captions.csv \
        --bucket your-bucket-name \
        --manifest-out clips_manifest.csv
"""

import argparse
import csv
import json
import os

from google.cloud import storage
from tqdm import tqdm

VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm")
LEVELS = ("L0", "L1", "L2", "L3")


def local_filename(clip_name: str, level: str) -> str:
    return f"{clip_name}_{level}.mp4"


def public_url(bucket_name: str, blob_name: str) -> str:
    return f"https://storage.googleapis.com/{bucket_name}/{blob_name}"


def normalize_path(p: str) -> str:
    """clip_table_cache.csv / manifest.json store Windows-style relative
    paths (backslashes) — normalize to forward slashes for os.path.join to
    handle correctly on any platform."""
    return p.replace("\\", "/")


def load_clip_table(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_captions(path: str):
    """clip_name -> human_caption, from a CSV with columns clip_name,human_caption."""
    with open(path, newline="", encoding="utf-8") as f:
        return {r["clip_name"]: r["human_caption"] for r in csv.DictReader(f)}


def load_degradation_records(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["records"]


def scan_level_folder(video_root: str, level: str):
    """Returns [(clip_name, relative_path)] for every video file directly
    inside video_root/{level}/, clip_name taken from the filename stem."""
    level_dir = os.path.join(video_root, level)
    if not os.path.isdir(level_dir):
        return []
    found = []
    for fname in sorted(os.listdir(level_dir)):
        stem, ext = os.path.splitext(fname)
        if ext.lower() in VIDEO_EXTENSIONS:
            found.append((stem, f"{level}/{fname}"))
    return found


def build_manifest_from_folder(video_root: str, captions_path: str):
    """Builds manifest rows by scanning video_root/L0, L1, L2, L3
    subfolders directly — no separate clip_table_cache.csv / manifest.json
    needed. GT captions (for L0 only) come from a clip_name,human_caption
    CSV, joined by filename stem."""
    captions = load_captions(captions_path)
    rows = []
    missing_captions = []

    for level in LEVELS:
        stage = "1_defiguration" if level == "L0" else "2_degradation"
        found = scan_level_folder(video_root, level)
        if not found:
            print(f"  ! No video files found in {os.path.join(video_root, level)}")
        for clip_name, rel_path in found:
            gt_caption = ""
            if level == "L0":
                gt_caption = captions.get(clip_name, "")
                if clip_name not in captions:
                    missing_captions.append(clip_name)
            rows.append({
                "clip_name": clip_name,
                "level": level,
                "stage": stage,
                "gt_caption": gt_caption,
                "video_url": "",
                "local_path": rel_path,
            })

    if missing_captions:
        print(f"  ! {len(missing_captions)} L0 clip(s) have no matching caption "
              f"in {captions_path} (uploaded with empty gt_caption): "
              f"{', '.join(missing_captions[:5])}"
              f"{', ...' if len(missing_captions) > 5 else ''}")

    return rows


def build_manifest_from_sources(clip_table_path: str, manifest_json_path: str,
                                 only_degraded_clips: bool = False):
    """Builds manifest rows (matching the same schema assign_clips.py
    expects) directly from clip_table_cache.csv (L0 base videos + GT
    captions) and manifest.json (L1-L3 degraded records). Each row also
    carries a 'local_path' field (relative, forward-slash-normalized) used
    to locate the file for upload — assign_clips.py ignores this extra
    column since it only reads named fields it needs.

    If only_degraded_clips=True, L0 rows are restricted to clip_names that
    also appear in manifest.json — use this if clip_table_cache.csv covers
    the full corpus but only a subset was selected for degradation/annotation
    (common: clip_table_cache.csv has every clip, manifest.json only the
    ones sampled for the main study)."""
    rows = []
    degraded_records = load_degradation_records(manifest_json_path)
    degraded_clip_names = {rec["clip"] for rec in degraded_records}

    for r in load_clip_table(clip_table_path):
        if only_degraded_clips and r["clip_name"] not in degraded_clip_names:
            continue
        rows.append({
            "clip_name": r["clip_name"],
            "level": "L0",
            "stage": "1_defiguration",
            "gt_caption": r["human_caption"],
            "video_url": "",
            "local_path": normalize_path(r["video_path"]),
        })

    for rec in degraded_records:
        rows.append({
            "clip_name": rec["clip"],
            "level": rec["level"],
            "stage": "2_degradation",
            "gt_caption": "",
            "video_url": "",
            "local_path": normalize_path(rec["output"]),
        })

    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clip-table", help="Path to clip_table_cache.csv (mode A)")
    ap.add_argument("--manifest-json", help="Path to manifest.json (mode A)")
    ap.add_argument("--data-root", default=".",
                     help="Local root dir the relative paths in clip_table_cache.csv "
                          "/ manifest.json are resolved against (mode A)")
    ap.add_argument("--only-degraded-clips", action="store_true",
                     help="Restrict L0 rows to clips that also have L1-L3 "
                          "records in manifest.json (mode A)")
    ap.add_argument("--video-dir", help="Directory of local videos named "
                                         "'{clip_name}_{level}.mp4' (mode B)")
    ap.add_argument("--manifest-in", help="Pre-built manifest CSV missing video_url (mode B)")
    ap.add_argument("--video-root", help="Directory containing L0/L1/L2/L3 "
                                          "subfolders of videos (mode C)")
    ap.add_argument("--captions", help="CSV with clip_name,human_caption "
                                        "for L0 GT captions (mode C)")
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--manifest-out", required=True)
    args = ap.parse_args()

    if args.video_root and args.captions:
        rows = build_manifest_from_folder(args.video_root, args.captions)
        resolve_local_path = lambda r: os.path.join(args.video_root, r["local_path"])
    elif args.clip_table and args.manifest_json:
        rows = build_manifest_from_sources(args.clip_table, args.manifest_json,
                                            only_degraded_clips=args.only_degraded_clips)
        resolve_local_path = lambda r: os.path.join(args.data_root, r["local_path"])
    elif args.video_dir and args.manifest_in:
        with open(args.manifest_in, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        resolve_local_path = lambda r: os.path.join(args.video_dir, local_filename(r["clip_name"], r["level"]))
    else:
        raise SystemExit(
            "Provide one of: (--video-root and --captions) for mode C, "
            "(--clip-table and --manifest-json) for mode A, "
            "or (--video-dir and --manifest-in) for mode B."
        )

    client = storage.Client()
    bucket = client.bucket(args.bucket)

    uploaded, skipped = 0, 0
    for r in tqdm(rows, desc="Uploading videos", unit="video"):
        local_path = resolve_local_path(r)
        if not os.path.exists(local_path):
            tqdm.write(f"  ! Missing local file, skipping: {local_path}")
            skipped += 1
            continue

        blob_name = f"{r['clip_name']}/{r['level']}.mp4"
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(local_path, content_type="video/mp4")

        r["video_url"] = public_url(args.bucket, blob_name)
        uploaded += 1

    if rows:
        with open(args.manifest_out, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    print(f"Uploaded {uploaded} video(s), skipped {skipped} missing file(s).")
    print(f"Wrote manifest with video_url filled in: {args.manifest_out}")
    print("Next: python assign_clips.py --manifest " + args.manifest_out)


if __name__ == "__main__":
    main()