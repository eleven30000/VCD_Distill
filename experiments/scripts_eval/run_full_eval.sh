#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# run_full_eval.sh
# 在 POPE 的 3 個 dataset × 3 個 split (=9 組) 上評估三個模型：
#   1. Baseline  (llava-v1.5-7b)
#   2. VCD       (llava-v1.5-7b + runtime VCD)
#   3. VCDD-best (AW2 λ=0.9, max_w=5)
# 跳過已存在的 answer 檔案，不重複推理。
# ─────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

CKPT="./checkpoints/llava-v1.5-7b"
VCDD="./output/llava_vcdd_aw2_lambda09_lora"
VCDD_BASE="$CKPT"
OUT="./output/full_eval"
mkdir -p "$OUT"

# ── 資料集設定 ──────────────────────────────────────────────
declare -A IMG_DIR
IMG_DIR[coco]="./data/coco/val2014"
IMG_DIR[gqa]="./data/gqa/images"
IMG_DIR[aokvqa]="./data/coco/val2014"

DATASETS=(coco gqa aokvqa)
SPLITS=(random popular adversarial)

# ─────────────────────────────────────────────────────────────
# 推理函式
#   infer <tag> <model-path> [--model-base <base>] [vcd-flags...]
#         <img-dir> <question-file> <answers-file>
# ─────────────────────────────────────────────────────────────
infer() {
    local tag="$1"; shift
    local model_path="$1"; shift
    local img_dir ans_file q_file
    # Collect remaining model flags until we hit --img-dir sentinel
    local model_flags=()
    while [[ "$1" != "--IMG" ]]; do
        model_flags+=("$1"); shift
    done
    shift  # consume --IMG
    img_dir="$1"; shift
    q_file="$1"; shift
    ans_file="$1"; shift

    if [[ -f "$ans_file" ]]; then
        echo "  [skip] $tag — answers already exist: $ans_file"
        return
    fi
    echo
    echo ">>> [$tag]"
    echo "    question: $q_file"
    echo "    output  : $ans_file"
    python eval/object_hallucination_vqa_llava.py \
        --model-path "$model_path" \
        "${model_flags[@]}" \
        --image-folder "$img_dir" \
        --question-file "$q_file" \
        --answers-file  "$ans_file" \
        --seed 42
}

# ─────────────────────────────────────────────────────────────
# 主循環
# ─────────────────────────────────────────────────────────────
for ds in "${DATASETS[@]}"; do
    img="${IMG_DIR[$ds]}"
    for split in "${SPLITS[@]}"; do
        qfile="./data/POPE/${ds}/${ds}_pope_${split}.json"
        echo
        echo "════════════════════════════════════════"
        echo "  Dataset: $ds  Split: $split"
        echo "════════════════════════════════════════"

        # 1) Baseline
        infer "baseline_${ds}_${split}" \
            "$CKPT" \
            --IMG "$img" "$qfile" \
            "${OUT}/ans_baseline_${ds}_${split}.jsonl"

        # 2) VCD Runtime
        infer "vcd_${ds}_${split}" \
            "$CKPT" \
            --use_cd --cd_alpha 1.0 --cd_beta 0.1 --noise_step 500 \
            --IMG "$img" "$qfile" \
            "${OUT}/ans_vcd_${ds}_${split}.jsonl"

        # 3) VCDD-AW2 λ=0.9
        infer "vcdd_${ds}_${split}" \
            "$VCDD" \
            --model-base "$VCDD_BASE" \
            --IMG "$img" "$qfile" \
            "${OUT}/ans_vcdd_${ds}_${split}.jsonl"
    done
done

# ─────────────────────────────────────────────────────────────
# 評分 + 彙整
# ─────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════"
echo "  POPE Evaluation Summary"
echo "════════════════════════════════════════"
printf "%-35s | %8s | %8s | %8s | %8s | %6s\n" \
    "Model_Dataset_Split" "Accuracy" "F1" "Precision" "Recall" "Yes%"
printf "%s\n" "$(python3 -c "print('-'*90)")"

for ds in "${DATASETS[@]}"; do
    for split in "${SPLITS[@]}"; do
        qfile="./data/POPE/${ds}/${ds}_pope_${split}.json"
        for model in baseline vcd vcdd; do
            ans="${OUT}/ans_${model}_${ds}_${split}.jsonl"
            if [[ ! -f "$ans" ]]; then
                printf "%-35s | MISSING\n" "${model}_${ds}_${split}"
                continue
            fi
            result=$(python eval/eval_pope.py \
                --gt_files  "$qfile" \
                --gen_files "$ans" 2>/dev/null)
            acc=$(echo "$result" | grep "Accuracy" | awk '{print $2}')
            f1=$( echo "$result" | grep "F1"       | awk '{print $2}')
            prec=$(echo "$result" | grep "Precision" | awk '{print $2}')
            rec=$(echo "$result" | grep "Recall"    | awk '{print $2}')
            yes=$(echo "$result" | grep "^yes"      | awk '{print $2}')
            printf "%-35s | %8.4f | %8.4f | %9.4f | %8.4f | %5.3f\n" \
                "${model}_${ds}_${split}" "$acc" "$f1" "$prec" "$rec" "$yes"
        done
        echo ""
    done
done | tee "${OUT}/summary.txt"

echo
echo "Summary saved to: ${OUT}/summary.txt"
