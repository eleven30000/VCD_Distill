#!/usr/bin/env bash
# run_train_coco500.sh
# Train VCDD using the Offline Distillation method on COCO500 descriptions.

set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
IMG_FOLDER="./data/coco/val2014"
Q_FILE="./data/coco_desc_500.jsonl"
OUT_DIR="./output/llava_vcdd_offline_coco500_lora64_lambda0"

echo ">>> Starting Offline VCD Distillation (Teacher=Base, 3 passes) on 500 Open-Ended Descriptions..."
python src/vcdd_coco500.py \
    --model-path "$CKPT" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$Q_FILE" \
    --save-path "$OUT_DIR" \
    --base-reg-lambda 0.0 \
    --aw-scale 100.0 \
    --aw-max-weight 5.0 \
    --epochs 1 \
    --grad-accum 4 \
    --lr 2e-5 \
    --lora-r 64 \
    --lora-alpha 128

echo ">>> Distillation completed. Model saved to ${OUT_DIR}_llava_lora"
