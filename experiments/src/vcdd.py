"""
vcdd.py - VCD Distillation (VCDD) for LLaVA 1.5  (v3 – Plan-A: base-model KL regularization)

Architecture:
  Single model instance acts as both teacher and student.
  Teacher : torch.no_grad() + VCD contrast (clean+noisy) + Adaptive Plausibility Filter
  Student : trainable LoRA, learns VCD distribution while regularized toward base model.

Plan-A anti-forgetting (no CE, no GT labels required):
  Loss = KL(student ∥ VCD_teacher) + λ · KL(student ∥ base_model)

  The base-model KL term is obtained by temporarily disabling the LoRA adapter
  (model.disable_adapter_layers()) and running a clean forward pass under no_grad.
  This anchors the student to the original language model distribution, preventing
  catastrophic forgetting (including EOS generation failure) without any ground-truth
  label dependency.

Usage:
    python vcdd.py \
        --model-path /path/to/llava-v1.5-7b \
        --image-folder ./data/coco/val2014 \
        --question-file ./data/POPE/coco/coco_pope_random.json \
        --save-path ./output/llava_vcdd_v3 \
        --base-reg-lambda 0.5
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
    parser = argparse.ArgumentParser(description="VCD Distillation for LLaVA 1.5 (v2)")
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
    parser.add_argument("--base-reg-lambda", type=float, default=0.5,
                        help="Plan-A: weight λ for KL(student ∥ base_model) regularization. "
                             "0 = no regularization (original v2 behaviour). "
                             "Recommended range: 0.1–1.0")
    # Training
    parser.add_argument("--lr", type=float, default=2e-5,
                        help="Peak learning rate — 2e-5 is safe for LoRA distillation")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--warmup-ratio", type=float, default=0.03,
                        help="Fraction of total steps used for linear warmup")
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--max-steps", type=int, default=None,
                        help="Stop early (for sanity checks)")
    parser.add_argument("--save-path", type=str, default="./output/llava_vcdd")
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
# Returns input_ids (full sequence) and pred_mask (True at positions that
# predict answer tokens, i.e. the causal-shifted answer positions).
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
    answer = line.get("label", "").strip()
    if not answer:
        return None, None

    # Capitalise first letter to match LLaVA's generation style
    answer = answer[0].upper() + answer[1:]

    if model_config.mm_use_im_start_end:
        img_token = DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN + DEFAULT_IM_END_TOKEN
    else:
        img_token = DEFAULT_IMAGE_TOKEN

    question_text = img_token + "\n" + qs + " Please answer this question with one word."

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
# Teacher: compute VCD soft labels with Adaptive Plausibility Filter
# ─────────────────────────────────────────────────────────────────────────────
@torch.no_grad()
def teacher_soft_labels(model, input_ids, img_clean, img_noisy,
                        A, alpha, beta, temperature):
    """
    A: number of answer tokens

    The model expands the IMAGE_TOKEN_INDEX into 256 image features, so
    logit length = L_token + 255.  Answer tokens are always at the END,
    so predicting positions are always logits[-A-1 : -1].

    Returns soft_labels: (A, V) float32
    """
    out_clean = model(input_ids=input_ids, images=img_clean)
    logits_clean = out_clean.logits.float()   # (1, L_logit, V)

    out_noisy = model(input_ids=input_ids, images=img_noisy)
    logits_noisy = out_noisy.logits.float()   # (1, L_logit, V)

    # ── Adaptive Plausibility Filter ───────────────────────────────────
    # Only apply VCD contrast where the clean token probability is above β * max_prob.
    # Elsewhere fall back to clean logits to avoid amplifying near-zero tokens.
    p_clean = F.softmax(logits_clean / temperature, dim=-1)            # (1, L_logit, V)
    threshold = beta * p_clean.max(dim=-1, keepdim=True).values        # (1, L_logit, 1)
    plausible = p_clean >= threshold                                    # (1, L_logit, V)

    logits_vcd = torch.where(
        plausible,
        (1.0 + alpha) * logits_clean - alpha * logits_noisy,
        logits_clean,
    )  # (1, L_logit, V)

    # ── Slice the A prediction positions at the END of the logit sequence ────
    # logits[-A-1:-1] predict answer tokens [-A:] regardless of image expansion
    logits_answer = logits_vcd[:, -A - 1 : -1, :]    # (1, A, V)
    soft = F.softmax(logits_answer / temperature, dim=-1)
    return soft.squeeze(0)                             # (A, V)


# ─────────────────────────────────────────────────────────────────────────────
# Plan-A: Base-model soft labels (LoRA disabled, no_grad)
# Used as the KL regularization target to prevent catastrophic forgetting.
# ─────────────────────────────────────────────────────────────────────────────
@torch.no_grad()
def base_soft_labels(model, input_ids, img_clean, A, temperature):
    """
    Temporarily disable the LoRA adapter to get the ORIGINAL base model's
    output distribution.  This serves as an anchor: KL(student ∥ base) keeps
    the student from drifting too far from the pretrained distribution,
    which prevents catastrophic forgetting without any GT label dependency.

    Returns base_probs: (A, V) float32
    """
    model.disable_adapter_layers()
    out_base = model(input_ids=input_ids, images=img_clean)
    model.enable_adapter_layers()

    logits_base  = out_base.logits.float()                     # (1, L_logit, V)
    logits_ans   = logits_base[:, -A - 1 : -1, :]             # (1, A, V)
    base_probs   = F.softmax(logits_ans / temperature, dim=-1)
    return base_probs.squeeze(0)                               # (A, V)


# ─────────────────────────────────────────────────────────────────────────────
# KL-divergence distillation loss + optional Plan-A base-model regularization
# ─────────────────────────────────────────────────────────────────────────────
def distill_loss(student_logits, A, soft_labels, input_ids, temperature,
                 sft_only=False, base_probs=None, base_reg_lambda=0.0):
    """
    student_logits   : (1, L_logit, V)
    A                : number of answer tokens
    soft_labels      : (A, V)  Teacher VCD probabilities — None if sft_only
    input_ids        : (1, L_input)  Ground-truth token ids (used only in sft_only mode)
    base_probs       : (A, V)  Base-model probabilities for Plan-A regularization
                               (None disables Plan-A, i.e. original v2 behaviour)
    base_reg_lambda  : float   Weight λ for KL(student ∥ base) term
    """
    stu = student_logits[:, -A - 1 : -1, :]              # (1, A, V)
    log_probs      = F.log_softmax(stu.float() / temperature, dim=-1)
    log_probs_flat = log_probs.squeeze(0)                 # (A, V)

    if sft_only:
        # SFT-only: plain Cross-Entropy on GT labels (backward-compat mode)
        labels   = input_ids[:, -A:]                     # (1, A)
        ce_loss  = F.cross_entropy(stu.view(-1, stu.size(-1)), labels.view(-1))
        return ce_loss, torch.tensor(0.0), torch.tensor(0.0), ce_loss

    # ── KL toward VCD teacher ────────────────────────────────────────────────
    kl_vcd = F.kl_div(log_probs_flat, soft_labels,
                      reduction="batchmean") * (temperature ** 2)

    # ── Plan-A: KL toward base model (anti-forgetting anchor) ───────────────
    if base_probs is not None and base_reg_lambda > 0.0:
        kl_base = F.kl_div(log_probs_flat, base_probs,
                           reduction="batchmean") * (temperature ** 2)
    else:
        kl_base = torch.tensor(0.0)

    loss = kl_vcd + base_reg_lambda * kl_base
    return loss, kl_vcd, kl_base, torch.tensor(0.0)


# ─────────────────────────────────────────────────────────────────────────────
# Main training routine
# ─────────────────────────────────────────────────────────────────────────────
def train(args):
    set_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"

    plan_a = (not args.sft_only) and (args.base_reg_lambda > 0.0)

    print("=" * 60)
    print("VCD Distillation v3  [teacher-forcing + APF + LoRA]")
    if args.sft_only:
        print("  MODE         : SFT-only (Cross-Entropy only, No VCD Distillation)")
    elif plan_a:
        print(f"  MODE         : Plan-A  (KL_vcd + {args.base_reg_lambda}·KL_base, NO CE)")
    else:
        print("  MODE         : v2 (KL_vcd only, no CE, no base regularization)")
    print(f"  model-path   : {args.model_path}")
    print(f"  epochs       : {args.epochs}")
    print(f"  lr (peak)    : {args.lr}")
    print(f"  warmup-ratio : {args.warmup_ratio}")
    print(f"  cd-alpha     : {args.cd_alpha}  cd-beta: {args.cd_beta}  noise-step: {args.noise_step}")
    print(f"  temperature  : {args.temperature}")
    print(f"  lora-r       : {args.lora_r}")
    if plan_a:
        print(f"  base-reg-λ   : {args.base_reg_lambda}")
    print("=" * 60)

    # ── Load model ───────────────────────────────────────────────────────────
    print("\n[1/3] Loading model...")
    disable_torch_init()
    model_name = get_model_name_from_path(args.model_path)
    tokenizer, model, image_processor, _ = load_pretrained_model(
        args.model_path, args.model_base, model_name
    )

    # ── Wrap with LoRA ───────────────────────────────────────────────────────
    # Cover all attention projections + FFN layers for max capacity
    from peft import get_peft_model, LoraConfig, TaskType
    lora_config = LoraConfig(
        task_type=TaskType.CAUSAL_LM,
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        # Only attention query/value projections — avoids corrupting FFN knowledge storage
        target_modules=["q_proj", "v_proj"],
    )
    model = get_peft_model(model, lora_config)
    model.print_trainable_parameters()

    if torch.cuda.is_available():
        alloc = torch.cuda.memory_allocated() / 1024**3
        print(f"  VRAM after model+LoRA: {alloc:.1f} GB")

    # ── Load questions ───────────────────────────────────────────────────────
    print("[2/3] Loading questions...")
    questions = [json.loads(q) for q in open(os.path.expanduser(args.question_file))]
    print(f"  {len(questions)} questions loaded")

    # ── Optimizer + LR scheduler ─────────────────────────────────────────────
    total_updates = math.ceil(len(questions) * args.epochs / args.grad_accum)
    warmup_steps  = int(total_updates * args.warmup_ratio)

    # Ensure save_path contains both 'llava' and 'lora' in the basename.
    # LLaVA's builder.py uses get_model_name_from_path() (the last path segment)
    # to dispatch model loading:
    #   'llava' in name  →  LLaVA branch (initialises vision tower + image_processor)
    #   'lora'  in name  →  LoRA branch  (loads adapter weights + merges)
    # Missing either keyword causes silent failures at inference time.
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

    # ── Training loop ────────────────────────────────────────────────────────
    print("[3/3] Training...")
    os.makedirs(save_path, exist_ok=True)
    global_step  = 0
    running_loss = 0.0
    optimizer.zero_grad()

    for epoch in range(args.epochs):
        print(f"\n── Epoch {epoch + 1}/{args.epochs} ──────────────────────────")

        for step, line in enumerate(tqdm(questions, desc=f"Epoch {epoch+1}")):

            # ── Build teacher-forcing input ──────────────────────────────────
            input_ids, A = build_teacher_forcing_input(
                line, tokenizer, model.config, args.conv_mode
            )
            if input_ids is None:
                continue
            input_ids = input_ids.to(device)

            # ── Prepare images ───────────────────────────────────────────────
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
            # ── Step 1a: Teacher VCD soft labels (no grad) ───────────────────
            if not args.sft_only:
                img_noisy = add_diffusion_noise(
                    image_tensor, args.noise_step
                ).unsqueeze(0).half().to(device)

                # Teacher forward: adapter ENABLED (LoRA at this point is still
                # very close to identity at the start, so teacher ≈ base teacher)
                model.eval()
                soft_labels = teacher_soft_labels(
                    model, input_ids, img_clean, img_noisy,
                    A, args.cd_alpha, args.cd_beta, args.temperature
                )  # (A, V)
            else:
                soft_labels = None

            # ── Step 1b: Plan-A — base model soft labels (adapter DISABLED) ──
            if plan_a:
                # disable_adapter_layers() / enable_adapter_layers() are called
                # inside base_soft_labels() and are safe to call repeatedly.
                base_probs = base_soft_labels(
                    model, input_ids, img_clean, A, args.temperature
                )  # (A, V)
            else:
                base_probs = None

            # ── Step 2: Student forward (with grad) ──────────────────────────
            model.train()
            out_student = model(input_ids=input_ids, images=img_clean)
            loss, kl_vcd, kl_base, ce_loss = distill_loss(
                out_student.logits, A, soft_labels, input_ids, args.temperature,
                sft_only=args.sft_only,
                base_probs=base_probs,
                base_reg_lambda=args.base_reg_lambda,
            )
            loss = loss / args.grad_accum
            loss.backward()

            # ── NaN 偵測：跳過壞掉的 batch 避免汙染 weights ─────────────────
            if torch.isnan(loss) or torch.isinf(loss):
                tqdm.write(f"  [warn] step {step}: NaN/Inf loss, skipping batch")
                optimizer.zero_grad()
                running_loss = 0.0
                continue

            running_loss += loss.item() * args.grad_accum

            # ── Gradient accumulation update ─────────────────────────────────
            if (step + 1) % args.grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(
                    filter(lambda p: p.requires_grad, model.parameters()), 1.0
                )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad()
                global_step += 1

                avg_loss = running_loss / args.grad_accum
                running_loss = 0.0
                lr_now = scheduler.get_last_lr()[0]
                if plan_a:
                    tqdm.write(
                        f"  step {global_step:5d} | loss {avg_loss:.4f} "
                        f"(KL_vcd={kl_vcd.item():.4f}, KL_base={kl_base.item():.4f}) "
                        f"| lr {lr_now:.2e}"
                    )
                else:
                    tqdm.write(
                        f"  step {global_step:5d} | loss {avg_loss:.4f} "
                        f"(KL_vcd={kl_vcd.item():.4f}, CE={ce_loss.item():.4f}) "
                        f"| lr {lr_now:.2e}"
                    )

            if args.max_steps is not None and global_step >= args.max_steps:
                print(f"\n  max_steps={args.max_steps} reached, stopping early.")
                break

        if args.max_steps is not None and global_step >= args.max_steps:
            break

    # ── Save ─────────────────────────────────────────────────────────────────
    print(f"\nSaving to: {save_path}")
    model.save_pretrained(save_path)
    tokenizer.save_pretrained(save_path)

    # Files needed by LLaVA's builder.py for LoRA inference
    base_config = os.path.join(args.model_path, "config.json")
    if os.path.exists(base_config):
        shutil.copy(base_config, os.path.join(save_path, "config.json"))
    torch.save({}, os.path.join(save_path, "non_lora_trainables.bin"))
    # mm_projector.bin is needed by builder.py to load the vision projection layer
    mm_proj = os.path.join(args.model_path, "mm_projector.bin")
    if os.path.exists(mm_proj):
        shutil.copy(mm_proj, os.path.join(save_path, "mm_projector.bin"))


    print("  LoRA adapter + inference files saved.")
    print(f"  Inference: --model-path {save_path} --model-base {args.model_path}")
    print("\nVCDD v2 distillation complete!")


if __name__ == "__main__":
    args = parse_args()
    train(args)

