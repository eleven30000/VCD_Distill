#!/usr/bin/env bash
# run_eval_all_openended.sh
set -euo pipefail
cd "$(dirname "$0")"

MODEL_PATH="./output/llava_vcdd_aw_openended_lora64_lambda09"
BASE_MODEL="./checkpoints/llava-v1.5-7b"
POPE_OUT_DIR="./output/full_eval"
MME_OUT_DIR="./output/mme_eval"
POPE_TAG="vcdd_openended"
MME_TAG="vcdd_openended"

DATA_DIR="data/POPE"
DATASETS=("coco" "aokvqa" "gqa")
SPLITS=("random" "popular" "adversarial")

mkdir -p "$POPE_OUT_DIR"
mkdir -p "$MME_OUT_DIR"

echo "============================================================"
echo "  [Open-Ended VCDD] Evaluating on POPE Benchmarks"
echo "============================================================"

for ds in "${DATASETS[@]}"; do
    for split in "${SPLITS[@]}"; do
        qfile="${DATA_DIR}/${ds}/${ds}_pope_${split}.json"
        
        if [[ "$ds" == "coco" ]]; then
            img_path="data/coco/val2014"
        elif [[ "$ds" == "gqa" ]]; then
            img_path="data/gqa/images"
        elif [[ "$ds" == "aokvqa" ]]; then
            img_path="data/coco/val2014"
        fi

        ans_file="${POPE_OUT_DIR}/ans_${POPE_TAG}_${ds}_${split}.jsonl"

        if [[ -f "$ans_file" ]]; then
            echo "  [Skip] ${POPE_TAG} on ${ds}_${split} (already evaluated)"
        else
            echo ">> Running inference: ${ds}_${split}"
            python eval/object_hallucination_vqa_llava.py \
                --model-path "$MODEL_PATH" \
                --model-base "$BASE_MODEL" \
                --question-file "$qfile" \
                --image-folder "$img_path" \
                --answers-file "$ans_file" \
                --seed 42
        fi
    done
done

SUMMARY_FILE="${POPE_OUT_DIR}/summary_${POPE_TAG}_pope.txt"
{
printf "%-35s | Accuracy |       F1 | Precision |   Recall |   Yes%%\n" "Dataset_Split"
echo "------------------------------------------------------------------------------------------"
for ds in "${DATASETS[@]}"; do
    for split in "${SPLITS[@]}"; do
        qfile="${DATA_DIR}/${ds}/${ds}_pope_${split}.json"
        ans_file="${POPE_OUT_DIR}/ans_${POPE_TAG}_${ds}_${split}.jsonl"
        
        metrics=$(python eval/eval_pope.py \
            --gt_files "$qfile" \
            --gen_files "$ans_file" 2>/dev/null)
        
        acc=$(echo "$metrics" | grep "Accuracy" | awk '{print $2}')
        prec=$(echo "$metrics" | grep "Precision" | awk '{print $2}')
        rec=$(echo "$metrics" | grep "Recall" | awk '{print $2}')
        f1=$(echo "$metrics" | grep "F1" | awk '{print $2}')
        yes_r=$(echo "$metrics" | grep "^yes" | awk '{print $2}')
        
        printf "%-35s | %8.4f | %8.4f | %9.4f | %8.4f | %5.3f\n" \
            "${ds}_${split}" "$acc" "$f1" "$prec" "$rec" "$yes_r"
    done
    echo ""
done
} | tee "$SUMMARY_FILE"
echo "POPE Eval Done! Summary saved to ${SUMMARY_FILE}"

echo "============================================================"
echo "  [Open-Ended VCDD] Evaluating on MME Benchmark"
echo "============================================================"
QFILE="./data/MME_questions.jsonl"
IMGDIR="./data/MME_images"
mme_ans_file="${MME_OUT_DIR}/ans_${MME_TAG}.jsonl"

echo ">> Inference on MME..."
python eval/mme_vqa_llava.py \
    --model-path   "$MODEL_PATH" \
    --model-base   "$BASE_MODEL" \
    --question-file "$QFILE" \
    --image-folder  "$IMGDIR" \
    --answers-file  "$mme_ans_file" \
    --seed 42

echo "────────────────────────────────────────"
echo "  Evaluation: $MME_TAG"
echo "────────────────────────────────────────"
python eval/eval_mme.py --answers-file "$mme_ans_file" | tee "${MME_OUT_DIR}/summary_${MME_TAG}.txt"
echo "MME Eval Done! Summary saved to ${MME_OUT_DIR}/summary_${MME_TAG}.txt"
