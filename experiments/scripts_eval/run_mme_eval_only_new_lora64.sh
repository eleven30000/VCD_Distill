#!/usr/bin/env bash
# run_mme_eval_only_new_lora64.sh
# 在 MME 上評估剛剛用 MME 資料集蒸餾出來的 VCDD 模型 (LoRA R=64)
set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
NEW_VCDD="./output/llava_vcdd_offline_smoothaw0p5_lora64_lambda01"
OUT="./output/mme_eval"
QFILE="./data/MME_questions.jsonl"
IMGDIR="./data/MME_images"
TAG="vcdd_offline_smoothaw0p5_lora64_lambda01"

mkdir -p "$OUT"
ans_file="${OUT}/ans_${TAG}.jsonl"

echo ">>> Inference on $TAG..."
python eval/mme_vqa_llava.py \
    --model-path   "$NEW_VCDD" \
    --model-base   "$CKPT" \
    --question-file "$QFILE" \
    --image-folder  "$IMGDIR" \
    --answers-file  "$ans_file" \
    --seed 42

echo
echo "────────────────────────────────────────"
echo "  Evaluation: $TAG"
echo "────────────────────────────────────────"
python eval/eval_mme.py --answers-file "$ans_file" | tee "${OUT}/summary_${TAG}.txt"
echo
echo "Done! Summary saved to ${OUT}/summary_${TAG}.txt"
