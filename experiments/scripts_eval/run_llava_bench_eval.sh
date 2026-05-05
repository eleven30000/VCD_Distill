#!/usr/bin/env bash
# run_llava_bench_eval.sh
set -euo pipefail
cd "$(dirname "$0")"

QFILE="./data/llava-bench-in-the-wild/questions.jsonl"
IMG_FOLDER="./data/llava-bench-in-the-wild/images"
OUT_DIR="./output/llava_bench_eval"
mkdir -p "$OUT_DIR"

CKPT_BASE="./checkpoints/llava-v1.5-7b"
CKPT_VCDD_NEW="./output/llava_vcdd_aw_openended_lora64_lambda09"

echo "=========================================="
echo "  Evaluating Baseline on LLaVA-Bench..."
echo "=========================================="
python eval/open_ended_vqa.py \
    --model-path "$CKPT_BASE" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$QFILE" \
    --answers-file "$OUT_DIR/ans_baseline.jsonl" \
    --seed 42

echo "=========================================="
echo "  Evaluating VCD (Runtime) on LLaVA-Bench..."
echo "=========================================="
python eval/open_ended_vqa.py \
    --model-path "$CKPT_BASE" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$QFILE" \
    --answers-file "$OUT_DIR/ans_vcd.jsonl" \
    --use_cd --cd_alpha 1.0 --cd_beta 0.1 --noise_step 500 \
    --seed 42

echo "=========================================="
echo "  Evaluating VCDD (Open-Ended) on LLaVA-Bench..."
echo "=========================================="
python eval/open_ended_vqa.py \
    --model-path "$CKPT_VCDD_NEW" \
    --model-base "$CKPT_BASE" \
    --image-folder "$IMG_FOLDER" \
    --question-file "$QFILE" \
    --answers-file "$OUT_DIR/ans_vcdd_openended.jsonl" \
    --seed 42

echo "=========================================="
echo "  Generating Blind Test Markdown Report..."
echo "=========================================="
python generate_llava_bench_report.py

echo "Done! The blind test markdown file is ready."
