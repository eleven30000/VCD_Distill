#!/usr/bin/env python3
"""
eval/eval_mme.py  —  Official MME scoring formula

  Accuracy   = (correct questions / total questions) × 100       (0~100)
  Accuracy+  = (images where BOTH q1+q2 correct / total images) × 100  (0~100)
  Category Score = Accuracy + Accuracy+                          (0~200)

  Perception total  = sum of 10 category scores  (max 2000)
  Cognition  total  = sum of  4 category scores  (max  800)

這才是論文 Table 2 裡出現 175.83 這類帶小數點數字的由來。

Usage:
  python eval/eval_mme.py --answers-file ./output/mme_eval/ans_baseline.jsonl
"""
import json
import argparse
from collections import defaultdict

PERCEPTION_CATS = {
    "existence", "count", "position", "color",
    "posters", "celebrity", "scene", "landmark", "artwork", "OCR",
}
COGNITION_CATS = {
    "commonsense_reasoning", "numerical_calculation",
    "text_translation", "code_reasoning",
}


def parse_answer(text: str) -> str:
    t = text.strip().lower()
    if t.startswith("yes"):
        return "Yes"
    elif t.startswith("no"):
        return "No"
    for word in t.split():
        w = word.strip(".,!?;:")
        if w == "yes":
            return "Yes"
        if w == "no":
            return "No"
    return text.strip()[:3].capitalize()


def evaluate(answers_file: str):
    predictions = [json.loads(l) for l in open(answers_file)]

    # Group: category → image_id → [(pred, gt), ...]
    grouped = defaultdict(lambda: defaultdict(list))
    for p in predictions:
        cat    = p.get("category", "unknown")
        img_id = p.get("image_id", p["question_id"])
        pred   = parse_answer(p["text"])
        gt     = p["gt_answer"]
        grouped[cat][img_id].append((pred, gt))

    # ── Per-category scoring ──────────────────────────────────────────────
    print(f"\n{'Category':<28} {'Acc':>8} {'Acc+':>8} {'Score':>10}  (/200)")
    print("-" * 62)

    perception_mme = 0.0
    cognition_mme  = 0.0
    results = {}

    for cat in sorted(grouped.keys()):
        img_dict     = grouped[cat]
        n_imgs       = len(img_dict)
        n_qs         = sum(len(v) for v in img_dict.values())
        correct_qs   = 0
        correct_imgs = 0

        for img_id, pairs in img_dict.items():
            n_correct = sum(1 for pred, gt in pairs if pred == gt)
            correct_qs   += n_correct
            if n_correct == len(pairs):   # ALL questions for this image correct
                correct_imgs += 1

        acc      = correct_qs   / n_qs   * 100 if n_qs   > 0 else 0.0
        acc_plus = correct_imgs / n_imgs * 100 if n_imgs > 0 else 0.0
        mme      = acc + acc_plus   # max 200

        results[cat] = {"acc": acc, "acc_plus": acc_plus, "mme": mme,
                        "n_imgs": n_imgs, "n_qs": n_qs}

        print(f"  {cat:<26} {acc:>7.2f}% {acc_plus:>7.2f}% {mme:>10.2f}")

        if cat in PERCEPTION_CATS:
            perception_mme += mme
        elif cat in COGNITION_CATS:
            cognition_mme  += mme

    # ── Summary ───────────────────────────────────────────────────────────
    total_mme = perception_mme + cognition_mme
    print("-" * 62)
    print(f"  {'Perception (P)':<26} {perception_mme:>38.2f}  (/2000)")
    print(f"  {'Cognition  (C)':<26} {cognition_mme:>38.2f}  (/800)")
    print(f"  {'TOTAL      (P+C)':<26} {total_mme:>38.2f}  (/2800)")
    print()
    print(f"  Perception Score : {perception_mme:.2f}")
    print(f"  Cognition  Score : {cognition_mme:.2f}")
    print(f"  Total      Score : {total_mme:.2f}")

    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers-file", type=str, required=True)
    args = parser.parse_args()
    evaluate(args.answers_file)
