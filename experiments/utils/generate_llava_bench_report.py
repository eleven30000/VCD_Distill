import json
import os
import random

def load_jsonl(path):
    data = {}
    with open(path, 'r') as f:
        for line in f:
            obj = json.loads(line)
            data[obj['question_id']] = obj
    return data

def main():
    q_file = "data/llava-bench-in-the-wild/questions.jsonl"
    base_file = "output/llava_bench_eval/ans_baseline.jsonl"
    vcd_file = "output/llava_bench_eval/ans_vcd.jsonl"
    vcdd_file = "output/llava_bench_eval/ans_vcdd_openended.jsonl"
    
    questions = load_jsonl(q_file)
    try:
        ans_base = load_jsonl(base_file)
        ans_vcd = load_jsonl(vcd_file)
        ans_vcdd = load_jsonl(vcdd_file)
    except FileNotFoundError as e:
        print(f"Error loading files: {e}. Please ensure you've run the inference script.")
        return

    output_md = "llava_bench_blind_test.md"
    
    # We will shuffle the order of the models to make it a true blind test.
    # A mapping from A/B/C to the real models will be stored at the bottom using HTML comments.
    
    with open(output_md, 'w', encoding='utf-8') as f:
        f.write("# LLaVA-Bench Open-Ended Blind Test\n\n")
        f.write("此文件列出了 60 道開放式問題的三種模型回答。模型順序固定如下：\n")
        f.write("- **Model A**: LLaVA-1.5 Baseline (原始模型)\n")
        f.write("- **Model B**: Teacher_VCD (Runtime 解碼老師)\n")
        f.write("- **Model C**: Student_VCDD (蒸餾學生)\n\n")
        
        
        true_mapping_log = []
        
        for qid, q in questions.items():
            img_path = f"data/llava-bench-in-the-wild/images/{q['image']}"
            f.write(f"## Question {qid} ({q.get('category', 'unknown')})\n")
            f.write(f"**Image:** `{img_path}`  \n")
            f.write(f"（您可以透過 VSCode 直接點擊路徑，或是使用檔案總管查看圖片）\n\n")
            f.write(f"**Prompt:** {q['text']}\n\n")
            
            models = [
                ("Baseline", ans_base.get(qid, {}).get("text", "N/A")),
                ("Teacher_VCD", ans_vcd.get(qid, {}).get("text", "N/A")),
                ("Student_VCDD", ans_vcdd.get(qid, {}).get("text", "N/A"))
            ]
            # Do not shuffle; keep fixed order: Baseline, VCD, VCDD
            
            labels = ["Baseline", "Teacher_VCD", "Student_VCDD"]
            mapping_for_q = {"qid": qid}
            
            for label, (model_name, answer) in zip(labels, models):
                mapping_for_q[label] = model_name
                f.write(f"### Model {label}\n")
                f.write(f"{answer}\n\n")
                
            true_mapping_log.append(mapping_for_q)
            f.write("---\n\n")
            
        # Writing decoding ring is no longer necessary as labels are fixed.

    print(f"Blind test report generated at {output_md}")

if __name__ == "__main__":
    random.seed(42)
    main()
