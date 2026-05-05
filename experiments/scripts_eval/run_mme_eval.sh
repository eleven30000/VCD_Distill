#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run_mme_eval.sh
# 在 MME 上評估 Baseline / VCD Runtime / VCDD-AW2 λ=0.9
# 先執行 mme_prepare.py，再跑三個模型的推理與評分。
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
VCDD="./output/llava_vcdd_aw2_lambda09_lora"
OUT="./output/mme_eval"
QFILE="./data/MME_questions.jsonl"
IMGDIR="./data/MME_images"

mkdir -p "$OUT"

# ── Step 1: 準備資料（只第一次要做）───────────────────────────
if [[ ! -f "$QFILE" ]]; then
    echo ">>> Preparing MME dataset..."
    python eval/mme_prepare.py
else
    echo ">>> MME dataset already prepared, skipping."
fi

# ── 推理函式 ─────────────────────────────────────────────────
infer() {
    local tag="$1"; shift
    local model_path="$1"; shift
    local extra_flags=("$@")
    local ans_file="${OUT}/ans_${tag}.jsonl"

    if [[ -f "$ans_file" ]]; then
        echo "  [skip] $tag — already exists"
        return
    fi
    echo
    echo ">>> Inference: $tag"
    python eval/mme_vqa_llava.py \
        --model-path   "$model_path" \
        --question-file "$QFILE" \
        --image-folder  "$IMGDIR" \
        --answers-file  "$ans_file" \
        --seed 42 \
        "${extra_flags[@]}"
}

# ── Step 2: 推理 ─────────────────────────────────────────────
infer "baseline" "$CKPT"

infer "vcd"      "$CKPT" \
    --use_cd --cd_alpha 1.0 --cd_beta 0.1 --noise_step 500

infer "vcdd" "$VCDD" \
    --model-base "$CKPT"

# ── Step 3: 評分彙整 ─────────────────────────────────────────
{
echo "════════════════════════════════════════"
echo "  MME Evaluation Summary"
echo "════════════════════════════════════════"
for tag in baseline vcd vcdd; do
    echo
    echo "────────────────────────────────────────"
    echo "  Model: $tag"
    echo "────────────────────────────────────────"
    python eval/eval_mme.py --answers-file "${OUT}/ans_${tag}.jsonl"
done
} | tee "${OUT}/mme_summary.txt"

echo
echo "Summary saved to: ${OUT}/mme_summary.txt"
