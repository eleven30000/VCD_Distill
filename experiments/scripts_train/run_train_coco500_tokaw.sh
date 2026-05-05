#!/usr/bin/env bash
# run_train_coco500_tokaw.sh
# Token-Level AW version of Offline VCDD on COCO 500.
# 每個 answer token 有自己的 correction weight w_i = clamp(KL_i * aw_scale, max)
# 而非全序列共用一個 scalar。

set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
IMG_FOLDER="./data/coco/val2014"
Q_FILE="./data/coco_desc_500.jsonl"
OUT_DIR="./output/llava_vcdd_offline_tokaw_lora64_lambda01"

echo ">>> Starting Offline VCD Distillation [Token-Level AW] (λ=0.1) on 500 Open-Ended Descriptions..."
python src/vcdd_coco500_tokaw.py \
    --model-path "$CKPT" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$Q_FILE" \
    --save-path "$OUT_DIR" \
    --base-reg-lambda 0.1 \
    --aw-scale 100.0 \
    --aw-max-weight 5.0 \
    --epochs 1 \
    --grad-accum 4 \
    --lr 2e-5 \
    --lora-r 64 \
    --lora-alpha 128 \
    2>&1 | tee ./trainlog/train_offline_tokaw_lambda01.log

echo ">>> Distillation completed. Model saved to ${OUT_DIR}"
