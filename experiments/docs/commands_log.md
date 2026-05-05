### distill
``` terminal
nohup python vcdd_aw.py \
    --model-path ./checkpoints/llava-v1.5-7b \
    --image-folder ./data/coco/val2014 \
    --question-file ./data/POPE/coco/coco_pope_random.json \
    --save-path ./output/llava_vcdd_aw_lambda09_scale_10 \
    --base-reg-lambda 0.9 \
    --aw-scale 100.0 \
    --aw-max-weight 10.0 \
    --epochs 1 \
    --grad-accum 4 \
    --lora-r 16 \
    --seed 42 \
> vcdd_aw_lambda09_scale_10.log 2>&1 &
echo "PID: $!"
tail -f vcdd_aw_lambda09_scale_10.log
```
### eval
``` terminal
nohup python eval/object_hallucination_vqa_llava.py \
    --model-path ./output/llava_vcdd_aw_lambda09_scale_10_lora \
    --model-base ./checkpoints/llava-v1.5-7b \
    --image-folder ./data/coco/val2014 \
    --question-file ./data/POPE/coco/coco_pope_random.json \
    --answers-file ./output/eval_vcdd_aw_lambda09_scale_10_lora.jsonl \
    --seed 42 \
> eval_vcdd_aw_lambda09_scale_10_lora.log 2>&1 &
echo "PID: $!"
tail -f eval_vcdd_aw_lambda09_scale_10_lora.log
```
``` terminal
python eval/eval_pope.py \
    --gt_files ./data/POPE/coco/coco_pope_random.json \
    --gen_files ./output/eval_vcdd_aw_lambda09_scale_10_lora.jsonl

```
## Baseline(llava-v1.5-7b)
100%|██████████| 3000/3000 [07:05<00:00,  7.05it/s]
Precision: 0.9232053422370617
Recall: 0.7373333333333333
F1: 0.8198665678280207
Accuracy: 0.838
yes: 0.3993333333333333
unknow: 0.0
## VCD
100%|██████████| 3000/3000 [13:27<00:00,  3.71it/s]
Precision: 0.9324104234527687
Recall: 0.7633333333333333
F1: 0.8394428152492668
Accuracy: 0.854
yes: 0.4093333333333333
unknow: 0.0
## v6
### λ=0.5 e1
100%|██████████| 3000/3000 [07:12<00:00,  6.93it/s]
Precision: 0.8667152221412965
Recall: 0.7933333333333333
F1: 0.8284023668639053
Accuracy: 0.8356666666666667
yes: 0.45766666666666667
unknow: 0.0

### λ=0.7 e1 
100%|██████████| 3000/3000 [07:09<00:00,  6.98it/s]
Precision: 0.8882175226586103
Recall: 0.784
F1: 0.8328611898016998
Accuracy: 0.8426666666666667
yes: 0.44133333333333336
unknow: 0.0

### λ=0.9 e1
100%|██████████| 3000/3000 [07:08<00:00,  7.00it/s]
Precision: 0.8994586233565351
Recall: 0.7753333333333333
F1: 0.8327962764052989
Accuracy: 0.8443333333333334
yes: 0.431
unknow: 0.0

## 前期實驗小結
VCD 同時提升了 Precision 和 Recall（兩者都進步），而我們的 VCDD 是用 Precision 換 Recall。這說明了：**VCDD 還沒有真正學到 VCD「更精確辨識物件」的能力，而是學到了「更勇敢說 Yes」的傾向**

從降低幻覺的角度看，有兩種詮釋：
樂觀詮釋：F1 和 Accuracy 都超越 Baseline，Recall 大幅提升意味著模型漏報物件的情況減少了，整體更貼近現實。這本身就是一種幻覺減少。
嚴格詮釋：Precision 下降代表模型確實多說了一些「不存在的物件」，這才是幻覺（hallucination）的核心定義。VCD 能兩者兼顧，VCDD 目前還做不到。

根本原因：
VCD 的效果來自於對比兩個視覺輸入（乾淨 vs 加噪）來校正 logit，讓模型在「看得清楚的時候更確定，看不清楚的時候更保守」。這個機制同時提升了 Precision 和 Recall。
VCDD 蒸餾的只是這個機制的輸出分布，而不是它的推理邏輯。所以模型學到的是「VCD 傾向說 Yes」這個表面現象，而不是「有充分視覺證據才說 Yes」的深層邏輯。
如果想讓 VCDD 的 Precision 也超過 Baseline，可能需要在蒸餾時加入不確定性懲罰（對 soft label 中 yes token 的熵進行調節），或者考慮換一個蒸餾目標（不只蒸餾 yes/no 分布，而是蒸餾對視覺 token 的注意力模式）。這是更深層的研究問題了。


## v7
### 想法一：Agreement-Weighted 信心蒸餾
核心想法：與teacher logits輸出像的話->更有信心 不像的話->沒有信心 而不是一昧的模仿輸出
在計算 Teacher Soft Label 時，同時計算「VCD 修正了多少」：
```python
# 量化 VCD 對這個樣本的修正強度
kl_correction = F.kl_div(
    F.log_softmax(logits_clean[:, -A-1:-1, :] / T, dim=-1),  # clean 分布
    F.softmax(logits_vcd[:, -A-1:-1, :] / T, dim=-1),        # vcd 分布
    reduction="batchmean"
)

# 用修正強度當作 VCD 蒸餾的權重
correction_weight = kl_correction.detach().clamp(0, 5)  # 避免極端值

# Loss
loss = correction_weight * KL(student ∥ vcd_teacher) + λ * KL(student ∥ base)

```
### λ=0.9 e1
Precision: 0.8768115942028986
Recall: 0.8066666666666666
F1: 0.8402777777777779
Accuracy: 0.8466666666666667
yes: 0.46
unknow: 0.0
### λ=1.1 e1 aw_scale=5
Precision: 0.8853550295857988
Recall: 0.798
F1: 0.8394109396914445
Accuracy: 0.8473333333333334
yes: 0.45066666666666666
unknow: 0.0
### λ=0.9 e1 aw_scale=10
Precision: 0.8767222625090645
Recall: 0.806
F1: 0.8398749565821466
Accuracy: 0.8463333333333334
yes: 0.45966666666666667
unknow: 0.0

### 想法二：Visual Attention 蒸餾
核心洞察
VCD 的反幻覺能力來自於：用加噪圖片讓模型「看不清楚」→ 找出哪些 token 是形成幻覺的視覺依據 → 對比修正。
如果能讓 student 直接學會「在乾淨圖片上就更正確地關注視覺 token」，就真正內化了這個能力