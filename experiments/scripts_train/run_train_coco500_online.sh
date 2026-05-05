#!/usr/bin/env bash
# run_train_coco500_online.sh
# Train VCDD using the open-ended descriptions dataset (coco_desc_500.jsonl).

set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
IMG_FOLDER="./data/coco/val2014"
Q_FILE="./data/coco_desc_500.jsonl"
OUT_DIR="./output/llava_vcdd_aw_openended_lora64_lambda09"

echo ">>> Starting VCDD-AW distillation on 500 COCO-500 Online (AW) (LoRA R=64)..."
python src/vcdd_coco500_online.py \
    --model-path "$CKPT" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$Q_FILE" \
    --save-path "$OUT_DIR" \
    --base-reg-lambda 0.9 \
    --aw-scale 100.0 \
    --aw-max-weight 5.0 \
    --epochs 1 \
    --grad-accum 4 \
    --lr 2e-5 \
    --lora-r 64 \
    --lora-alpha 128

echo ">>> Distillation completed. Model saved to $OUT_DIR"
