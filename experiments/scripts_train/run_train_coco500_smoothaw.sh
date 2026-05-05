#!/usr/bin/env bash
# run_train_coco500_smoothaw.sh
# Smooth-AW Offline VCDD: 融合 Scalar AW 與 Token AW
# w_i = β × mean_KL + (1-β) × KL_i
# 建議先試 beta=0.5（各佔一半），再根據結果調整
set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
IMG_FOLDER="./data/coco/val2014"
Q_FILE="./data/coco_desc_500.jsonl"
AW_BETA=0.8    # ← 主要旋鈕：0.0=Token AW, 0.5=balanced, 1.0=Scalar AW
BETA_TAG=$(echo "$AW_BETA" | tr '.' 'p')   # 0.8 → 0p8
OUT_DIR="./output/llava_vcdd_offline_smoothaw${BETA_TAG}_lora64_lambda01"

mkdir -p trainlog

echo ">>> Starting Offline VCD Distillation [Smooth-AW β=${AW_BETA}] (λ=0.1)..."
python src/vcdd_coco500_smoothaw.py \
    --model-path "$CKPT" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$Q_FILE" \
    --save-path "$OUT_DIR" \
    --base-reg-lambda 0.1 \
    --aw-scale 100.0 \
    --aw-max-weight 5.0 \
    --aw-beta "$AW_BETA" \
    --epochs 1 \
    --grad-accum 4 \
    --lr 2e-5 \
    --lora-r 64 \
    --lora-alpha 128 \
    2>&1 | tee "./trainlog/train_offline_smoothaw${BETA_TAG}_lambda01.log"

echo ">>> Done. Model saved to ${OUT_DIR}"
