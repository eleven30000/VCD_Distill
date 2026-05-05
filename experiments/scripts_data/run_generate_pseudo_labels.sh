#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

qfile="./data/coco_desc_questions.jsonl"
ansfile="./output/coco_desc_vcd_answers.jsonl"
finalfile="./data/coco_desc_500.jsonl"
ckpt="./checkpoints/llava-v1.5-7b"
imgdir="./data/coco/val2014"

echo ">>> Generating VCD pseudo-labels for open-ended description..."
python eval/open_ended_vqa.py \
    --model-path "$ckpt" \
    --image-folder "$imgdir" \
    --question-file "$qfile" \
    --answers-file "$ansfile" \
    --use_cd --cd_alpha 1.0 --cd_beta 0.1 --noise_step 500 \
    --seed 42

echo ">>> Formatting to vcdd_open_ended.py training format..."
python -c "
import json
with open('$qfile', 'r') as f_q, open('$ansfile', 'r') as f_a, open('$finalfile', 'w') as f_out:
    questions = {json.loads(line)['question_id']: json.loads(line) for line in f_q}
    for line in f_a:
        ans_obj = json.loads(line)
        qid = ans_obj['question_id']
        q_text = questions[qid]['text']
        img = questions[qid]['image']
        out_obj = {
            'question_id': qid,
            'image': img,
            'text': q_text,
            'label': ans_obj['text']
        }
        f_out.write(json.dumps(out_obj) + '\n')
"
echo "Done! The final training set is at $finalfile"
