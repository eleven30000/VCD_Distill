#!/usr/bin/env bash
# run_pope_eval_mme_trained.sh
# 測試 MME 訓練版模型在原本 9 個 POPE 資料集上的泛化能力

set -euo pipefail
cd "$(dirname "$0")"

MME_MODEL="./output/llava_vcdd_aw_mme_lambda09_lora"
BASE_MODEL="./checkpoints/llava-v1.5-7b"
OUT_DIR="./output/full_eval"
TAG="vcdd_mme_trained"

DATA_DIR="data/POPE"
DATASETS=("coco" "aokvqa" "gqa")
SPLITS=("random" "popular" "adversarial")

mkdir -p "$OUT_DIR"

echo "============================================================"
echo "  [MME-Trained VCDD] Evaluating on POPE Benchmarks"
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

        ans_file="${OUT_DIR}/ans_${TAG}_${ds}_${split}.jsonl"

        if [[ -f "$ans_file" ]]; then
            echo "  [Skip] ${TAG} on ${ds}_${split} (already evaluated)"
        else
            echo ">> Running inference: ${ds}_${split}"
            python eval/object_hallucination_vqa_llava.py \
                --model-path "$MME_MODEL" \
                --model-base "$BASE_MODEL" \
                --question-file "$qfile" \
                --image-folder "$img_path" \
                --answers-file "$ans_file" \
                --seed 42
        fi
    done
done

echo "============================================================"
echo "  Evaluation completed. Summarizing..."
echo "============================================================"

# 計算分數寫進專屬 summary
SUMMARY_FILE="${OUT_DIR}/summary_${TAG}_pope.txt"
{
printf "%-35s | Accuracy |       F1 | Precision |   Recall |   Yes%%\n" "Dataset_Split"
echo "------------------------------------------------------------------------------------------"
for ds in "${DATASETS[@]}"; do
    for split in "${SPLITS[@]}"; do
        qfile="${DATA_DIR}/${ds}/${ds}_pope_${split}.json"
        ans_file="${OUT_DIR}/ans_${TAG}_${ds}_${split}.jsonl"
        
        # 執行原本的 eval 腳本捕捉輸出
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
    echo "" # 空行分隔 dataset
done
} | tee "$SUMMARY_FILE"

echo "Done! Summary saved to ${SUMMARY_FILE}"
