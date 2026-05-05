#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

MME_MODEL="./output/llava_vcdd_offline_smoothaw0p8_lora64_lambda01"
BASE_MODEL="./checkpoints/llava-v1.5-7b"
OUT_DIR="./output/mme_eval"
MME_TAG="vcdd_offline_smoothaw0p8_lora64_lambda01"

QFILE="./data/MME_questions.jsonl"
IMGDIR="./data/MME_images"
mme_ans_file="${OUT_DIR}/ans_${MME_TAG}.jsonl"

mkdir -p "$OUT_DIR"

echo "============================================================"
echo "  [Offline VCDD λ=0.1] Evaluating on MME Benchmark"
echo "============================================================"

if [[ -f "$mme_ans_file" ]]; then
    echo "  [Skip] ${MME_TAG} on MME (already evaluated)"
else
    echo ">> Running inference on MME..."
    python eval/mme_vqa_llava.py \
        --model-path "$MME_MODEL" \
        --model-base "$BASE_MODEL" \
        --question-file "$QFILE" \
        --image-folder "$IMGDIR" \
        --answers-file "$mme_ans_file" \
        --seed 42
fi

echo "────────────────────────────────────────"
echo "  Evaluation: $MME_TAG"
echo "────────────────────────────────────────"
python eval/eval_mme.py --answers-file "$mme_ans_file" | tee "${OUT_DIR}/summary_${MME_TAG}.txt"
echo "MME Eval Done! Summary saved to ${OUT_DIR}/summary_${MME_TAG}.txt"
