#!/usr/bin/env bash
# run_train_mme.sh
# Train VCDD-AW2 using the MME dataset as the teacher-forcing source.

set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
IMG_FOLDER="./data/MME_images"
Q_FILE="./data/MME_questions.jsonl"
OUT_DIR="./output/llava_vcdd_aw_mme_lambda09_lora"

echo ">>> Starting VCDD-AW distillation on MME Benchmark..."
python src/vcdd_aw.py \
    --model-path "$CKPT" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$Q_FILE" \
    --save-path "$OUT_DIR" \
    --base-reg-lambda 0.9 \
    --aw-scale 100.0 \
    --aw-max-weight 5.0 \
    --epochs 1 \
    --grad-accum 4 \
    --lr 2e-5

echo ">>> Distillation completed. Model saved to $OUT_DIR"
