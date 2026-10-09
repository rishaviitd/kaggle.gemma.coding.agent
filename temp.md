What I understood:
- Current data has 129 tasks split as:
  - train: 76
  - dev: 27
  - val: 27
  - one HTTPX task is intentionally present in both dev and val, so there are 129 unique tasks.
- Current manifests use the old difficulty lens: patch size + issue length. I will replace that lens.
- New split plan:
  - multi-big-patch: 40 tasks where reference patch has more than 1 changed file OR more than 100 edited lines.
  - Remaining: 89 tasks.
  - Split those 89 into:
    - train: 20
    - dev: 34
    - val: 35
- The 89-task split will be balanced using:
  1. Whether base + all available iterations never solved it.
  2. Number of recorded successful solves.
  3. Patch-size band: 1–10, 11–25, 26–50, 51–100 lines.
  4. Repository mix where possible.
- Train will contain a higher share of difficult tasks, but not only difficult tasks. Target train mix:
  - 11 never solved
  - 4 solved 1–3 times
  - 5 solved 4+ times
- I will update/create:
  - data/train/manifest.csv
  - data/dev/manifest.csv
  - data/val/manifest.csv
  - data/multi-big-patch/manifest.csv
  - corresponding tasks.jsonl files
  - a reproducible script that regenerates all four splits deterministically.
- I will preserve the real shared assets in data/assets/. The split folders contain symlinks under snapshots/, graph/, and embeddings/. To reflect a moved task, its symlink references need to be relocated between split folders; the underlying asset files will not be deleted.

this seems good to me here 
