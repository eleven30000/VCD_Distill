#!/usr/bin/env python3
"""
eval/mme_prepare.py
從 lmms-lab/MME 的 parquet 格式中：
  1. 解壓圖片到 ./data/MME_images/
  2. 產生 JSONL 問題文件 ./data/MME_questions.jsonl

每張圖有 2 題（一個 Yes、一個 No），用同一個 image_id 分組。
Unique question_id = "{category}/{image}.png_{idx}"
"""
import pyarrow.parquet as pq
import io, os, glob, json
from PIL import Image
from collections import defaultdict
from tqdm import tqdm

PARQUET_DIR  = "./data/MME/data"
IMG_OUT_DIR  = "./data/MME_images"
JSONL_OUT    = "./data/MME_questions.jsonl"

os.makedirs(IMG_OUT_DIR, exist_ok=True)

# Count rows per question_id to build unique IDs
image_counter = defaultdict(int)
rows = []

print("Loading parquet shards...")
for pf in sorted(glob.glob(os.path.join(PARQUET_DIR, "*.parquet"))):
    table = pq.read_table(pf)
    n = len(table)
    print(f"  {os.path.basename(pf)}: {n} rows")
    for i in tqdm(range(n), desc=os.path.basename(pf), leave=False):
        qid      = table["question_id"][i].as_py()   # e.g. "existence/0001.png"
        cat      = table["category"][i].as_py()
        question = table["question"][i].as_py().replace("\n", " ").strip()
        answer   = table["answer"][i].as_py()
        img_data = table["image"][i].as_py()          # {"bytes": ..., "path": ...}
        img_bytes = img_data["bytes"]

        # --- save image (once per image_id) ---
        img_rel = qid                                  # relative path inside MME_images
        img_abs = os.path.join(IMG_OUT_DIR, img_rel)
        os.makedirs(os.path.dirname(img_abs), exist_ok=True)
        if not os.path.exists(img_abs):
            pil = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            pil.save(img_abs)

        # --- build unique question_id ---
        row_idx       = image_counter[qid]
        image_counter[qid] += 1
        unique_qid    = f"{qid}_{row_idx}"             # e.g. "existence/0001.png_0"

        rows.append({
            "question_id": unique_qid,   # unique per question
            "image_id":    qid,          # grouping key (same for Yes/No pair)
            "category":    cat,
            "image":       img_rel,      # relative path within MME_images
            "text":        question,     # already includes "Please answer yes or no."
            "answer":      answer,       # GT: "Yes" or "No"
        })

print(f"\nWriting {len(rows)} questions to {JSONL_OUT}")
with open(JSONL_OUT, "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")

print("Done.")
print(f"  Images: {IMG_OUT_DIR}")
print(f"  Questions: {JSONL_OUT}")
