#!/usr/bin/env bash
# run_llava_bench_first4.sh
# 用前 4 張 LLaVA-Bench 圖片比較 Baseline vs Offline Scalar AW λ=0.1
set -euo pipefail
cd "$(dirname "$0")"

QFILE_FULL="./data/llava-bench-in-the-wild/questions.jsonl"
QFILE_4="./data/llava_bench_first4.jsonl"
IMG_FOLDER="./data/llava-bench-in-the-wild/images"
OUT_DIR="./output/llava_bench_eval"
CKPT_BASE="./checkpoints/llava-v1.5-7b"
CKPT_OFFLINE="./output/llava_vcdd_offline_smoothaw0p8_lora64_lambda01"

mkdir -p "$OUT_DIR"

# Step 1: 建立前 4 張圖的 question subset
echo ">>> Filtering first 4 images..."
python3 - <<'PYEOF'
import json
with open('./data/llava-bench-in-the-wild/questions.jsonl') as f:
    lines = [json.loads(l) for l in f]
imgs = []
for l in lines:
    if l['image'] not in imgs:
        imgs.append(l['image'])
    if len(imgs) == 4:
        break
subset = [l for l in lines if l['image'] in imgs]
print(f"First 4 images: {imgs}")
print(f"Total questions: {len(subset)}")
with open('./data/llava_bench_first4.jsonl', 'w') as f:
    for l in subset:
        f.write(json.dumps(l)+'\n')
PYEOF

echo ">>> Done. Question file: $QFILE_4"
echo ""

# Step 2: Baseline inference
echo "==========================================="
echo "  [1/2] Baseline on first 4 images"
echo "==========================================="
python eval/llava_bench_vqa.py \
    --model-path "$CKPT_BASE" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$QFILE_4" \
    --answers-file "$OUT_DIR/ans_baseline_first4.jsonl" \
    --seed 42

# Step 3: Offline Scalar AW λ=0.1 inference
echo ""
echo "==========================================="
echo "  [2/2] Offline Scalar AW λ=0.1 on first 4"
echo "==========================================="
python eval/llava_bench_vqa.py \
    --model-path "$CKPT_OFFLINE" \
    --model-base "$CKPT_BASE" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$QFILE_4" \
    --answers-file "$OUT_DIR/ans_offline_smoothaw0p8_lora64_lambda01_first4.jsonl" \
    --seed 42

# Step 4: 並排顯示對比
echo ""
echo "==========================================="
echo "  Side-by-side comparison"
echo "==========================================="
python3 - <<'PYEOF'
import json

base = {r['question_id']: r for r in (json.loads(l) for l in open('./output/llava_bench_eval/ans_baseline_first4.jsonl'))}
vcdd = {r['question_id']: r for r in (json.loads(l) for l in open('./output/llava_bench_eval/ans_offline_smoothaw0p8_lora64_lambda01_first4.jsonl'))}
qs   = [json.loads(l) for l in open('./data/llava_bench_first4.jsonl')]

print()
for q in qs:
    qid = q['question_id']
    print(f"{'='*70}")
    print(f"[Image: {q['image']}]  [Q{qid}] [{q['category'].upper()}]")
    print(f"Q: {q['text']}")
    print()
    print(f"[Baseline]\n{base[qid]['text']}")
    print()
    print(f"[Offline λ=0.1]\n{vcdd[qid]['text']}")
    print()
PYEOF

echo "Done!"
