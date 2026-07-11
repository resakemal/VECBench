# VidEpiCal Multi-Annotator Web App — Setup

## 1. Database (Supabase, free tier)
1. Create a project at supabase.com.
2. In the SQL editor, run `schema.sql`.
3. Get the connection string: Project Settings → Database → Connection string
   (use the "URI" / pooled connection format).

## 2. Video storage (Google Cloud Storage)
1. Create a bucket and make its objects publicly readable:
   ```bash
   gcloud storage buckets create gs://YOUR_BUCKET --location=us-central1
   gcloud storage buckets add-iam-policy-binding gs://YOUR_BUCKET \
       --member=allUsers --role=roles/storage.objectViewer
   ```
   (Skip the IAM binding if you'd rather not make the bucket public — see
   "Signed URLs instead of public" below.)

   **Or via the browser console**, if you'd rather not use `gcloud`:
   1. Go to [console.cloud.google.com](https://console.cloud.google.com) and confirm the correct project is selected in the top bar.
   2. Open the navigation menu (☰, top left) → **Cloud Storage** → **Buckets**.
   3. Click **Create**.
   4. **Name your bucket** — must be globally unique across all of GCP (e.g. `videpical-clips-yourname`). Click Continue.
   5. **Choose where to store your data** — pick **Region** (cheaper than multi-region, and you don't need the redundancy for this) and select a region close to you or your annotators, e.g. `us-central1`. Click Continue.
   6. **Choose a storage class** — leave as **Standard**. Click Continue.
   7. **Choose how to control access to objects** — set **Access control** to **Uniform** (required for the public-access step below to work cleanly). Under this step you'll also see **Prevent public access** — **uncheck/disable** the public access prevention toggle here, since you want annotators to load videos without authentication. Click Continue.
   8. Leave the remaining defaults (no need for object versioning, retention policy, or encryption changes) and click **Create**.
   9. Once created, open the bucket → **Permissions** tab → **Grant Access**.
   10. Under **New principals**, type `allUsers`. Under **Role**, select **Cloud Storage → Storage Object Viewer**. Click **Save**. Confirm the "public access" warning dialog if it appears.
   11. If step 7's public-access toggle was greyed out or step 10 fails with an "Public Access Prevention" error, your organization has an org-policy enforcing it — you'd need to either ask whoever manages the GCP org to allow public buckets for this project, or fall back to signed URLs (see below).

   You can also drag-and-drop video files directly into the bucket via the console's **Objects** tab to spot-check that public access works (click an uploaded file → copy its **Public URL** → open it in a new browser tab) before running the upload script on the full set.

2. Install tooling deps locally (not part of the deployed app):
   ```bash
   pip install -r requirements-tools.txt
   gcloud auth application-default login   # so the script can authenticate
   ```
3. Build the manifest and upload — three ways depending on what you have:

   **C) A folder with L0-L3 subfolders** (recommended if that's how your
   videos are already organized) — a directory containing `L0/`, `L1/`,
   `L2/`, `L3/` subfolders, each holding video files named
   `{clip_name}.mp4` (`.mov`/`.mkv`/`.webm` also recognized), plus a
   captions CSV (`clip_name,human_caption`) used as the GT caption for L0
   clips:
   ```bash
   python gcs_upload_videos.py \
       --video-root ./clips_by_level \
       --captions argus_captions.csv \
       --bucket YOUR_BUCKET \
       --manifest-out clips_manifest.csv
   ```
   `clip_name` is taken from each file's name (without extension) and
   joined against the captions CSV — clips found in `L0/` with no matching
   row in the captions file are still uploaded, just with an empty
   `gt_caption` (a warning is printed listing which ones, so you can check
   whether that's expected — e.g. clips outside this study's sample — or a
   naming mismatch worth fixing).

   **A) Directly from your pipeline outputs**
   (`clip_table_cache.csv` + `manifest.json`) — no manual manifest CSV
   needed:
   ```bash
   python gcs_upload_videos.py \
       --clip-table clip_table_cache.csv \
       --manifest-json manifest.json \
       --data-root /path/to/your/project/data \
       --bucket YOUR_BUCKET \
       --manifest-out clips_manifest.csv
   ```
   `--data-root` is the local directory the relative paths inside those two
   files resolve against (they use Windows-style relative paths like
   `argus_data\videos\X.mp4` and `exp_1\degraded\L1\X.mp4` — the script
   normalizes separators and joins them onto `--data-root`).
   `manifest.json`'s `level` field is already `L1`/`L2`/`L3` (from
   `video_degrade_v3.py --mode actual`), so no P0→L0 remapping happens here
   — `assign_clips.py`'s filter step just passes these through unchanged.

   If `clip_table_cache.csv` covers your full corpus but `manifest.json`
   only has degraded versions for a subset (common — check by comparing row
   counts), add `--only-degraded-clips` to skip L0 rows for clips that were
   never selected for the main study:
   ```bash
   python gcs_upload_videos.py \
       --clip-table clip_table_cache.csv \
       --manifest-json manifest.json \
       --data-root /path/to/your/project/data \
       --only-degraded-clips \
       --bucket YOUR_BUCKET \
       --manifest-out clips_manifest.csv
   ```

   **B) A manually-built manifest CSV** (original workflow) — one row per
   clip-level, named to match local video files (`{clip_name}_{level}.mp4`,
   e.g. `0001_P0.mp4`):

   | Main-study label | Pilot equivalent |
   |---|---|
   | L0 (base) | P0 |
   | L1 | P5 |
   | L2 | P7 |
   | L3 | P9 |

   ```
   clip_name,level,stage,gt_caption,video_url
   0001,P0,1_defiguration,"A person walks a dog...",
   0001,P5,2_degradation,,
   0001,P7,2_degradation,,
   0001,P9,2_degradation,,
   ```
   ```bash
   python gcs_upload_videos.py \
       --video-dir ./clips \
       --bucket YOUR_BUCKET \
       --manifest-in clips_manifest_no_urls.csv \
       --manifest-out clips_manifest.csv
   ```
   `assign_clips.py` still handles the P0/P5/P7/P9 → L0-L3 filtering and
   relabeling for this path — nothing changes there. Per-clip VMAF isn't
   tracked in the schema — quality target is assumed fixed per level
   (L0=90, L1=60, L2=40, L3=20), shown to annotators as a nominal label
   rather than a measured value.

**Signed URLs instead of public objects:** if you'd rather not make the
bucket public, GCS supports V4 signed URLs, but they cap out at **7 days**
expiration when generated from a service account — since your annotation
window will likely run longer than that, you'd need a small script to
periodically regenerate and update `video_url` values in the `clips` table,
or a lightweight endpoint that mints a signed URL on demand. Given these are
non-sensitive video clips (no personal data per your ethics review), the
public-bucket route is simpler and is what the script above does by
default.

## 3. Populate assignments (two rounds, fresh-eyes design)

To avoid an annotator captioning a degraded level of a clip they've
already seen clean (recognition-memory contamination), L0 and L1-L3 are
assigned in two passes, to different people:

```bash
export DATABASE_URL="postgresql://..."

# Round 1: distribute L0 (de-figuration) across annotators
python assign_round1.py --manifest clips_manifest.csv \
    --round1-overlap-frac 0.08 --round1-overlap-group-size 2

# Round 2: distribute L1-L3, excluding whoever did that clip's L0
# (run right after round 1 — see note below)
python assign_round2.py --manifest clips_manifest.csv \
    --round2-overlap-frac 0.08
```

Edit the `annotators` list near the top of `assign_round1.py` first — set
real display names and access codes for your 5 annotators before running.

**You can run round 2 immediately after round 1** — it doesn't need round
1 to be *finished*, only *assigned* (it excludes annotators based on who's
scheduled to see a clip's L0, not who's submitted it yet). Individual
annotators just won't be able to open a degraded clip in the app until its
L0 caption is actually submitted — the app shows a "come back later"
message and a skip button in that case, so this paces naturally per clip
without you needing to enforce a global two-phase rollout.

**Overlap, in two places:**
- Round 1's overlap group (`--round1-overlap-group-size`, default 2) has
  *some but not all* annotators independently caption the same clip's L0,
  for L0 inter-annotator agreement. It's capped below `n_annotators - 3`
  automatically — if every annotator saw a clip's L0, nobody would be left
  to caption its degraded levels fresh in round 2.
- Round 2's overlap (`--round2-overlap-frac`) has every remaining
  eligible ("fresh") annotator independently caption every degraded level
  of a clip, for degradation-captioning agreement holding the L0 base
  fixed.

Clips where fewer than 3 annotators remain eligible after round 1's
exclusions are skipped and reported by `assign_round2.py` — widen the
annotator pool or lower `--round1-overlap-group-size` if this happens
often.

<details>
<summary>Older single-round script (assign_clips.py) — deprecated for multi-annotator use</summary>

`assign_clips.py` keeps a clip's full L0-L3 chain with one annotator, which
means the same person who wrote L0 also writes the degraded levels — they've
already seen the clean video, which is a known contamination risk. It's
kept only for solo/small-team use where that's acceptable. Use
`assign_round1.py` + `assign_round2.py` above for multi-annotator setups.
</details>

## 4. Deploy the app
1. Push this folder to a GitHub repo.
2. On share.streamlit.io, create a new app pointing at `app.py`.
3. In the app's Settings → Secrets, add:
   ```
   DATABASE_URL = "postgresql://..."
   ```
4. (Optional) Deploy `admin_dashboard.py` as a second Streamlit app for
   yourself, with an `ADMIN_PASSWORD` secret.

## 5. Set up the worked examples (optional but recommended)
Annotators see a worked example each time they enter the de-figuration or
degradation section. The caption text is already baked into `examples.py`;
you just need to host the example videos and paste their URLs in.

1. Upload the example clips to your bucket (L0 should be transcoded to
   browser-safe H.264 like your real L0 clips — an already-transcoded
   `L0.mp4` is provided):
   ```bash
   gcloud storage cp L0.mp4              gs://YOUR_BUCKET/_examples/L0.mp4
   gcloud storage cp Bbp-cdBWg0k_L1.mp4  gs://YOUR_BUCKET/_examples/L1.mp4
   gcloud storage cp Bbp-cdBWg0k_L2.mp4  gs://YOUR_BUCKET/_examples/L2.mp4
   gcloud storage cp Bbp-cdBWg0k_L3.mp4  gs://YOUR_BUCKET/_examples/L3.mp4
   ```
2. Fill in `EXAMPLE_VIDEO_URLS` at the top of `examples.py` with the
   resulting public URLs (e.g.
   `https://storage.googleapis.com/YOUR_BUCKET/_examples/L0.mp4`).

Any URL left as `""` just omits that video and shows the caption text only,
so the app works before you've uploaded them.

## 6. Onboard annotators
Send each annotator the app URL and their access code. That's the only
"login" — no accounts to create. After logging in they pick a section
(de-figuration first; degradation unlocks once all their L0 tasks are done).

## 7. Pull annotations back into your existing pipeline
```bash
export DATABASE_URL="postgresql://..."
python export_annotations.py --out annotations.csv
```
Writes `clip_name, level, stage, base_caption, human_caption`, plus
`annotator_name`, `is_overlap_subset`, and `submitted_at` appended at the
end — `compute_full_dimensions.py` shouldn't need any changes if it reads
columns by name. `base_caption` is reconstructed at export time (L0's own
GT caption for L0 rows; the clip's L0 human_caption for L1-L3 rows), since
it isn't stored redundantly on every annotation row. Hedge/claim counts and
VMAF are not included — hedge/claim counts are superseded by exact PIU
extraction downstream, and VMAF is assumed fixed per level rather than
measured per clip.

Run this periodically during annotation (e.g. weekly) to check progress
against your pipeline, not just at the end.

## Notes
- Re-running `assign_clips.py` is safe (idempotent) if you add more clips later
  — existing assignments/annotations aren't touched (`ON CONFLICT DO NOTHING`
  / `DO UPDATE` on the relevant fields only).
- If you need to reshuffle who's assigned what before annotation starts,
  just edit/delete rows in `assignments` directly in Supabase's table editor.