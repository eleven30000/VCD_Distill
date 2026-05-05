"""
vcdd_coco500_online.py - VCD Distillation with Agreement-Weighting (VCDD-AW, Online, COCO-500 dataset) (VCDD-AW) for LLaVA 1.5

Architecture:
  Single model instance acts as both teacher and student.
  Teacher : torch.no_grad() + VCD contrast (clean+noisy) + Adaptive Plausibility Filter
  Student : trainable LoRA, Agreement-Weighted VCD distillation + base-model regularization

Plan-A anti-forgetting (no CE, no GT labels required):
  Base loss = correction_weight * KL(student ∥ VCD_teacher)
            + λ · KL(student ∥ base_model)

Agreement-Weighting (AW) — the key innovation over vcdd.py:
  raw_cw           = KL(clean_probs ∥ VCD_probs)  at answer positions
  correction_weight = clamp(raw_cw * aw_scale, 0, aw_max_weight)

  - When VCD agrees with clean model (small KL) → raw_cw ≈ 0
    → correction_weight ≈ 0: little distillation pressure
  - When VCD strongly disagrees (large KL) → raw_cw is large
    → correction_weight is large: strong distillation pressure

  Scaling (aw_scale): the raw KL values are typically 0.001~0.05.
  Without scaling, correction_weight ≈ 0 for all samples and the model
  learns nothing. aw_scale amplifies the signal to a meaningful range.
  Default aw_scale=100 maps 0.001~0.05 → 0.1~5.0.

Usage:
    python vcdd_aw.py \\
        --model-path ./checkpoints/llava-v1.5-7b \\
        --image-folder ./data/coco/val2014 \\
        --question-file ./data/POPE/coco/coco_pope_random.json \\
        --save-path ./output/llava_vcdd_aw_lambda09 \\
        --base-reg-lambda 0.9 \\
        --aw-max-weight 5.0
"""

import argparse
import os
import sys
import json
import math
import shutil
import torch
import torch.nn.functional as F
from tqdm import tqdm
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from transformers import set_seed, get_cosine_schedule_with_warmup
from llava.model.builder import load_pretrained_model
from llava.constants import (IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_TOKEN,
                              DEFAULT_IM_START_TOKEN, DEFAULT_IM_END_TOKEN)
from llava.conversation import conv_templates
from llava.mm_utils import tokenizer_image_token, get_model_name_from_path
from llava.utils import disable_torch_init
from vcd_utils.vcd_add_noise import add_diffusion_noise


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
def parse_args():
    parser = argparse.ArgumentParser(description="VCD Distillation with Agreement-Weighting (VCDD-AW)")
    # Model
    parser.add_argument("--model-path", type=str, required=True)
    parser.add_argument("--model-base", type=str, default=None)
    parser.add_argument("--conv-mode", type=str, default="llava_v1")
    # Data
    parser.add_argument("--image-folder", type=str, required=True)
    parser.add_argument("--question-file", type=str, required=True,
                        help="POPE-style JSON with 'text', 'image', and 'label' fields")
    # VCD hyper-params
    parser.add_argument("--noise-step", type=int, default=500)
    parser.add_argument("--cd-alpha", type=float, default=1.0,
                        help="VCD alpha: logit_vcd = (1+a)*logit_clean - a*logit_noisy")
    parser.add_argument("--cd-beta", type=float, default=0.1,
                        help="Adaptive Plausibility Filter threshold (β in VCD paper)")
    # Distillation
    parser.add_argument("--sft-only", action="store_true",
                        help="Train with Cross-Entropy only (no KL distillation)")
    parser.add_argument("--temperature", type=float, default=2.0,
                        help="Softmax temperature T for soft labels (KL loss scaled by T^2)")
    parser.add_argument("--base-reg-lambda", type=float, default=0.9,
                        help="Weight λ for KL(student ∥ base_model) regularization. "
                             "0 = no regularization. Recommended range: 0.5–1.0")
    # Agreement-Weighting
    parser.add_argument("--aw-scale", type=float, default=100.0,
                        help="AW: multiply raw KL(clean ∥ VCD) by this factor before "
                             "clamping. Raw KL is typically 0.001~0.05; default 100 "
                             "maps that to 0.1~5.0. Increase if w is still near 0.")
    parser.add_argument("--aw-max-weight", type=float, default=5.0,
                        help="AW: upper clamp for scaled correction_weight. Default: 5.0")
    # Training
    parser.add_argument("--lr", type=float, default=2e-5,
                        help="Peak learning rate — 2e-5 is safe for LoRA distillation")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--warmup-ratio", type=float, default=0.03,
                        help="Fraction of total steps used for linear warmup")
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=None,
                        help="Stop early (for sanity checks)")
    parser.add_argument("--save-path", type=str, default="./output/llava_vcdd_aw")
    # LoRA
    parser.add_argument("--lora-r", type=int, default=16,
                        help="LoRA rank — keep small (16) to avoid catastrophic forgetting")
    parser.add_argument("--lora-alpha", type=int, default=32)
    parser.add_argument("--lora-dropout", type=float, default=0.05)
    # Misc
    parser.add_argument("--seed", type=int, default=42)
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────────────────────
# Build teacher-forcing input: [prompt + GT answer]
# ─────────────────────────────────────────────────────────────────────────────
def build_teacher_forcing_input(line, tokenizer, model_config, conv_mode):
    """
    Returns:
      input_ids (1, L): full [prompt + answer] token sequence
      A (int): number of answer tokens

    NOTE: The model expands the single IMAGE_TOKEN_INDEX into 256 image feature
    tokens internally, so logit length = L + 255, NOT L.
    We therefore do NOT return a boolean mask; callers use the last-A slice
    of the logit tensor which is stable regardless of image expansion.
    """
    qs = line["text"]
    answer = line.get("label", line.get("answer", "")).strip()
    if not answer:
        return None, None

    # Capitalise first letter to match LLaVA's generation style
    answer = answer[0].upper() + answer[1:]

    if model_config.mm_use_im_start_end:
        img_token = DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN + DEFAULT_IM_END_TOKEN
    else:
        img_token = DEFAULT_IMAGE_TOKEN

    question_text = img_token + "\n" + qs

    # Prompt only (no answer) – to count prompt tokens
    conv_q = conv_templates[conv_mode].copy()
    conv_q.append_message(conv_q.roles[0], question_text)
    conv_q.append_message(conv_q.roles[1], None)
    prompt_ids = tokenizer_image_token(
        conv_q.get_prompt(), tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
    )  # (P,)

    # Full sequence with answer
    conv_full = conv_templates[conv_mode].copy()
    conv_full.append_message(conv_full.roles[0], question_text)
    conv_full.append_message(conv_full.roles[1], answer)
    full_ids = tokenizer_image_token(
        conv_full.get_prompt(), tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
    )  # (P+A,)

    P = prompt_ids.shape[0]
    L = full_ids.shape[0]
    A = L - P   # number of answer tokens

    if A <= 0:
        return None, None

    return full_ids.unsqueeze(0), A   # (1, L), int


# ─────────────────────────────────────────────────────────────────────────────
# Teacher: compute VCD soft labels + AW correction signal
# ─────────────────────────────────────────────────────────────────────────────
@torch.no_grad()
def teacher_soft_labels(model, input_ids, img_clean, img_noisy,
                        A, alpha, beta, temperature):
    """
    Returns:
      soft_labels      : (A, V) float32  — VCD soft label probabilities
      correction_weight: scalar float    — KL(clean ∥ VCD) at answer positions
                                           Measures how much VCD actually changed
                                           the prediction for this sample.
                                           High  → VCD made a real correction
                                           Low   → VCD agreed with clean model
    """
    out_clean = model(input_ids=input_ids, images=img_clean)
    logits_clean = out_clean.logits.float()   # (1, L_logit, V)

    out_noisy = model(input_ids=input_ids, images=img_noisy)
    logits_noisy = out_noisy.logits.float()   # (1, L_logit, V)

    # ── Adaptive Plausibility Filter ──────────────────────────────────────────
    p_clean = F.softmax(logits_clean / temperature, dim=-1)            # (1, L_logit, V)
    threshold = beta * p_clean.max(dim=-1, keepdim=True).values        # (1, L_logit, 1)
    plausible = p_clean >= threshold                                    # (1, L_logit, V)

    logits_vcd = torch.where(
        plausible,
        (1.0 + alpha) * logits_clean - alpha * logits_noisy,
        logits_clean,
    )  # (1, L_logit, V)

    # ── Slice answer positions ────────────────────────────────────────────────
    logits_ans_clean = logits_clean[:, -A - 1 : -1, :]   # (1, A, V)
    logits_ans_vcd   = logits_vcd[:, -A - 1 : -1, :]     # (1, A, V)

    p_ans_clean = F.softmax(logits_ans_clean / temperature, dim=-1).squeeze(0)  # (A, V)
    p_ans_vcd   = F.softmax(logits_ans_vcd   / temperature, dim=-1).squeeze(0)  # (A, V)

    # ── Agreement-Weighting: KL(clean ∥ VCD) at answer positions ─────────────
    # This scalar tells us how much VCD diverged from the clean prediction.
    # We use it as a per-sample weight on the VCD distillation loss.
    # KL(P ∥ Q) = Σ P * log(P/Q)  — here P=clean, Q=vcd
    log_p_clean = torch.log(p_ans_clean + 1e-8)
    log_p_vcd   = torch.log(p_ans_vcd   + 1e-8)
    kl_clean_vcd = (p_ans_clean * (log_p_clean - log_p_vcd)).sum(-1).mean()  # scalar

    return p_ans_vcd, kl_clean_vcd.item()   # (A, V), float


# ─────────────────────────────────────────────────────────────────────────────
# Base-model soft labels (LoRA disabled, no_grad)
# ─────────────────────────────────────────────────────────────────────────────
@torch.no_grad()
def base_soft_labels(model, input_ids, img_clean, A, temperature):
    """
    Temporarily disable the LoRA adapter to get the ORIGINAL base model's
    output distribution. Serves as an anti-forgetting anchor.

    Returns base_probs: (A, V) float32
    """
    model.disable_adapter_layers()
    out_base = model(input_ids=input_ids, images=img_clean)
    model.enable_adapter_layers()

    logits_base = out_base.logits.float()                      # (1, L_logit, V)
    logits_ans  = logits_base[:, -A - 1 : -1, :]              # (1, A, V)
    base_probs  = F.softmax(logits_ans / temperature, dim=-1)
    return base_probs.squeeze(0)                               # (A, V)


# ─────────────────────────────────────────────────────────────────────────────
# Agreement-Weighted distillation loss
# ─────────────────────────────────────────────────────────────────────────────
def distill_loss(student_logits, A, soft_labels, input_ids, temperature,
                 sft_only=False, base_probs=None, base_reg_lambda=0.0,
                 correction_weight=1.0):
    """
    student_logits   : (1, L_logit, V)
    A                : number of answer tokens
    soft_labels      : (A, V)   VCD teacher probabilities
    input_ids        : (1, L)   GT token ids (only used in sft_only mode)
    base_probs       : (A, V)   Base model probabilities (anti-forgetting)
    base_reg_lambda  : float    Weight λ for KL(student ∥ base)
    correction_weight: float    AW scalar — KL(clean ∥ VCD) for this sample
                                High → VCD made a real correction, learn harder
                                Low  → VCD didn't change much, learn softly
    """
    stu = student_logits[:, -A - 1 : -1, :]              # (1, A, V)
    log_probs      = F.log_softmax(stu.float() / temperature, dim=-1)
    log_probs_flat = log_probs.squeeze(0)                 # (A, V)

    if sft_only:
        labels  = input_ids[:, -A:]
        ce_loss = F.cross_entropy(stu.view(-1, stu.size(-1)), labels.view(-1))
        return ce_loss, torch.tensor(0.0), torch.tensor(0.0), ce_loss

    # ── Agreement-Weighted KL toward VCD teacher ──────────────────────────────
    kl_vcd = F.kl_div(log_probs_flat, soft_labels,
                      reduction="batchmean") * (temperature ** 2)
    # Scale by correction_weight: larger when VCD diverged from clean
    kl_vcd_weighted = correction_weight * kl_vcd

    # ── KL toward base model (anti-forgetting anchor) ─────────────────────────
    if base_probs is not None and base_reg_lambda > 0.0:
        kl_base = F.kl_div(log_probs_flat, base_probs,
                           reduction="batchmean") * (temperature ** 2)
    else:
        kl_base = torch.tensor(0.0)

    loss = kl_vcd_weighted + base_reg_lambda * kl_base
    return loss, kl_vcd, kl_base, torch.tensor(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Main training routine
# ─────────────────────────────────────────────────────────────────────────────
def train(args):
    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    plan_a = (not args.sft_only) and (args.base_reg_lambda > 0.0)

    print("=" * 60)
    print("VCDD-AW  [Agreement-Weighted distillation + APF + LoRA]")
    if args.sft_only:
        print("  MODE         : SFT-only (Cross-Entropy, No VCD Distillation)")
    elif plan_a:
        print(f"  MODE         : AW + Plan-A  (w*KL_vcd + {args.base_reg_lambda}·KL_base)")
    else:
        print("  MODE         : AW only (w*KL_vcd, no base regularization)")
    print(f"  model-path   : {args.model_path}")
    print(f"  epochs       : {args.epochs}")
    print(f"  lr (peak)    : {args.lr}")
    print(f"  warmup-ratio : {args.warmup_ratio}")
    print(f"  cd-alpha     : {args.cd_alpha}  cd-beta: {args.cd_beta}  noise-step: {args.noise_step}")
    print(f"  temperature  : {args.temperature}")
    print(f"  lora-r       : {args.lora_r}")
    print(f"  aw-scale     : {args.aw_scale}  aw-max-w: {args.aw_max_weight}")
    if plan_a:
        print(f"  base-reg-λ   : {args.base_reg_lambda}")
    print("=" * 60)

    # ── Load model ────────────────────────────────────────────────────────────
    print("\n[1/3] Loading model...")
    disable_torch_init()
    model_name = get_model_name_from_path(args.model_path)
    tokenizer, model, image_processor, _ = load_pretrained_model(
        args.model_path, args.model_base, model_name
    )

    # ── Wrap with LoRA ────────────────────────────────────────────────────────
    from peft import get_peft_model, LoraConfig, TaskType
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        target_modules=["q_proj", "v_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.config.use_cache = False
    model.enable_input_require_grads()
    model.gradient_checkpointing_enable()
    model.print_trainable_parameters()

    if torch.cuda.is_available():
        alloc = torch.cuda.memory_allocated() / 1024**3
        print(f"  VRAM after model+LoRA: {alloc:.1f} GB")

    # ── Load questions ────────────────────────────────────────────────────────
    print("[2/3] Loading questions...")
    questions = [json.loads(q) for q in open(os.path.expanduser(args.question_file))]
    print(f"  {len(questions)} questions loaded")

    # ── Optimizer + LR scheduler ──────────────────────────────────────────────
    total_updates = math.ceil(len(questions) * args.epochs / args.grad_accum)
    warmup_steps  = int(total_updates * args.warmup_ratio)

    # Ensure save_path contains both 'llava' and 'lora' in the basename.
    save_path = args.save_path
    last_dir = os.path.basename(save_path.rstrip("/"))
    needs_suffix = []
    if "llava" not in last_dir.lower():
        needs_suffix.append("llava")
    if "lora" not in last_dir.lower():
        needs_suffix.append("lora")
    if needs_suffix:
        save_path = save_path.rstrip("/") + "_" + "_".join(needs_suffix)
        print(f"  [info] basename missing {needs_suffix}; saving to {save_path} instead")
    os.makedirs(save_path, exist_ok=True)

    optimizer = torch.optim.AdamW(
        filter(lambda p: p.requires_grad, model.parameters()),
        lr=args.lr,
        weight_decay=0.01,
    )
    scheduler = get_cosine_schedule_with_warmup(
        optimizer,
        num_warmup_steps=warmup_steps,
        num_training_steps=total_updates,
    )
    print(f"  Total update steps: {total_updates}  (warmup: {warmup_steps})")

    # ── Training loop ─────────────────────────────────────────────────────────
    print("[3/3] Training...")
    os.makedirs(save_path, exist_ok=True)
    global_step  = 0
    running_loss = 0.0
    running_cw   = 0.0   # track avg correction_weight for logging
    optimizer.zero_grad()

    for epoch in range(args.epochs):
        print(f"\n── Epoch {epoch + 1}/{args.epochs} ──────────────────────────")

        for step, line in enumerate(tqdm(questions, desc=f"Epoch {epoch+1}")):

            # ── Build teacher-forcing input ───────────────────────────────────
            input_ids, A = build_teacher_forcing_input(
                line, tokenizer, model.config, args.conv_mode
            )
            if input_ids is None:
                continue
            input_ids = input_ids.to(device)

            # ── Prepare images ────────────────────────────────────────────────
            image_path = os.path.join(args.image_folder, line["image"])
            try:
                image = Image.open(image_path).convert("RGB")
            except FileNotFoundError:
                print(f"\n  [warn] image not found: {image_path} — skipping")
                continue

            image_tensor = image_processor.preprocess(image, return_tensors="pt")[
                "pixel_values"
            ][0]
            img_clean = image_tensor.unsqueeze(0).half().to(device)

            # ── Step 1a: Teacher VCD soft labels + AW correction signal ───────
            if not args.sft_only:
                img_noisy = add_diffusion_noise(
                    image_tensor, args.noise_step
                ).unsqueeze(0).half().to(device)

                model.eval()
                soft_labels, raw_cw = teacher_soft_labels(
                    model, input_ids, img_clean, img_noisy,
                    A, args.cd_alpha, args.cd_beta, args.temperature
                )  # (A, V), float

                # Scale raw KL to a usable range, then clamp.
                # raw_cw is typically 0.001~0.05; aw_scale amplifies to ~0.1~5.0.
                correction_weight = min(raw_cw * args.aw_scale, args.aw_max_weight)
            else:
                soft_labels      = None
                correction_weight = 1.0
                raw_cw           = 1.0

            # ── Step 1b: Base model soft labels (adapter DISABLED) ────────────
            if plan_a:
                base_probs = base_soft_labels(
                    model, input_ids, img_clean, A, args.temperature
                )  # (A, V)
            else:
                base_probs = None

            # ── Step 2: Student forward (with grad) ───────────────────────────
            model.train()
            out_student = model(input_ids=input_ids, images=img_clean)
            loss, kl_vcd, kl_base, ce_loss = distill_loss(
                out_student.logits, A, soft_labels, input_ids, args.temperature,
                sft_only=args.sft_only,
                base_probs=base_probs,
                base_reg_lambda=args.base_reg_lambda,
                correction_weight=correction_weight,
            )
            loss = loss / args.grad_accum
            loss.backward()

            # ── NaN 偵測 ──────────────────────────────────────────────────────
            if torch.isnan(loss) or torch.isinf(loss):
                tqdm.write(f"  [warn] step {step}: NaN/Inf loss, skipping batch")
                optimizer.zero_grad()
                running_loss = 0.0
                running_cw   = 0.0
                continue

            running_loss += loss.item() * args.grad_accum
            running_cw   += correction_weight

            # ── Gradient accumulation update ──────────────────────────────────
            if (step + 1) % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(
                    filter(lambda p: p.requires_grad, model.parameters()), 1.0
                )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1

                avg_loss = running_loss / args.grad_accum
                avg_cw   = running_cw   / args.grad_accum
                running_loss = 0.0
                running_cw   = 0.0
                lr_now = scheduler.get_last_lr()[0]
                tqdm.write(
                    f"  step {global_step:5d} | loss {avg_loss:.4f} "
                    f"(KL_vcd={kl_vcd.item():.4f}×w={avg_cw:.3f}[raw≈{avg_cw/args.aw_scale:.4f}], "
                    f"KL_base={kl_base.item():.4f}) "
                    f"| lr {lr_now:.2e}"
                )

            if args.max_steps is not None and global_step >= args.max_steps:
                print(f"\n  max_steps={args.max_steps} reached, stopping early.")
                break

        if args.max_steps is not None and global_step >= args.max_steps:
            break

    # ── Save ──────────────────────────────────────────────────────────────────
    print(f"\nSaving to: {save_path}")
    model.save_pretrained(save_path)
    tokenizer.save_pretrained(save_path)

    # Files needed by LLaVA's builder.py for LoRA inference
    base_config = os.path.join(args.model_path, "config.json")
    if os.path.exists(base_config):
        shutil.copy(base_config, os.path.join(save_path, "config.json"))
    torch.save({}, os.path.join(save_path, "non_lora_trainables.bin"))
    mm_proj = os.path.join(args.model_path, "mm_projector.bin")
    if os.path.exists(mm_proj):
        shutil.copy(mm_proj, os.path.join(save_path, "mm_projector.bin"))

    print("  LoRA adapter + inference files saved.")
    print(f"  Inference: --model-path {save_path} --model-base {args.model_path}")
    print("\nVCDD-AW distillation complete!")


if __name__ == "__main__":
    args = parse_args()
    train(args)
