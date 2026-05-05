#!/usr/bin/env python3
"""
eval/mme_vqa_llava.py
推理腳本：在 MME 問題集上跑 LLaVA（支援 VCD 和 LoRA）。
輸入: --question-file data/MME_questions.jsonl
      --image-folder  data/MME_images
輸出: JSONL，每行含 question_id, category, image_id, text(prediction), gt_answer
"""
import argparse
import torch
import os
import json
from tqdm import tqdm
import sys

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from llava.constants import (IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_TOKEN,
                              DEFAULT_IM_START_TOKEN, DEFAULT_IM_END_TOKEN)
from llava.conversation import conv_templates, SeparatorStyle
from llava.model.builder import load_pretrained_model
from llava.utils import disable_torch_init
from llava.mm_utils import (tokenizer_image_token, get_model_name_from_path,
                             KeywordsStoppingCriteria)

from PIL import Image, ImageFile
ImageFile.LOAD_TRUNCATED_IMAGES = True
from transformers import set_seed
from vcd_utils.vcd_add_noise import add_diffusion_noise
from vcd_utils.vcd_sample import evolve_vcd_sampling
evolve_vcd_sampling()


def eval_model(args):
    disable_torch_init()
    model_path = os.path.expanduser(args.model_path)
    model_name = get_model_name_from_path(model_path)
    tokenizer, model, image_processor, context_len = load_pretrained_model(
        model_path, args.model_base, model_name)

    questions = [json.loads(q) for q in open(os.path.expanduser(args.question_file))]
    answers_file = os.path.expanduser(args.answers_file)
    os.makedirs(os.path.dirname(answers_file), exist_ok=True)
    ans_file = open(answers_file, "w")

    for line in tqdm(questions):
        idx        = line["question_id"]     # unique id
        image_id   = line.get("image_id", idx)
        category   = line.get("category", "")
        image_file = line["image"]           # relative path
        qs         = line["text"]            # already has "Please answer yes or no."
        gt_answer  = line.get("answer", "")

        # ── build LLaVA prompt ──────────────────────────────────────────
        if model.config.mm_use_im_start_end:
            qs_with_img = (DEFAULT_IM_START_TOKEN + DEFAULT_IMAGE_TOKEN
                           + DEFAULT_IM_END_TOKEN + "\n" + qs)
        else:
            qs_with_img = DEFAULT_IMAGE_TOKEN + "\n" + qs

        conv = conv_templates[args.conv_mode].copy()
        # MME question already contains "Please answer yes or no." — no extra suffix
        conv.append_message(conv.roles[0], qs_with_img)
        conv.append_message(conv.roles[1], None)
        prompt = conv.get_prompt()

        input_ids = tokenizer_image_token(
            prompt, tokenizer, IMAGE_TOKEN_INDEX, return_tensors="pt"
        ).unsqueeze(0).cuda()

        # ── load image ──────────────────────────────────────────────────
        image = Image.open(os.path.join(args.image_folder, image_file)).convert("RGB")
        image_tensor = image_processor.preprocess(image, return_tensors="pt")["pixel_values"][0]

        if args.use_cd:
            image_tensor_cd = add_diffusion_noise(image_tensor, args.noise_step)
        else:
            image_tensor_cd = None

        stop_str = conv.sep if conv.sep_style != SeparatorStyle.TWO else conv.sep2
        keywords  = [stop_str]
        stopping_criteria = KeywordsStoppingCriteria(keywords, tokenizer, input_ids)

        # ── generate ────────────────────────────────────────────────────
        with torch.inference_mode():
            output_ids = model.generate(
                input_ids,
                images=image_tensor.unsqueeze(0).half().cuda(),
                images_cd=(image_tensor_cd.unsqueeze(0).half().cuda()
                           if image_tensor_cd is not None else None),
                cd_alpha=args.cd_alpha,
                cd_beta=args.cd_beta,
                do_sample=True,
                temperature=args.temperature,
                top_p=args.top_p,
                top_k=args.top_k,
                max_new_tokens=64,
                use_cache=True,
            )

        input_token_len = input_ids.shape[1]
        outputs = tokenizer.batch_decode(
            output_ids[:, input_token_len:], skip_special_tokens=True)[0].strip()
        if outputs.endswith(stop_str):
            outputs = outputs[: -len(stop_str)]
        outputs = outputs.strip()

        ans_file.write(json.dumps({
            "question_id": idx,
            "image_id":    image_id,
            "category":    category,
            "image":       image_file,
            "text":        outputs,
            "gt_answer":   gt_answer,
            "prompt":      qs,
            "model_id":    model_name,
        }) + "\n")
        ans_file.flush()

    ans_file.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-path",    type=str, required=True)
    parser.add_argument("--model-base",    type=str, default=None)
    parser.add_argument("--image-folder",  type=str, default="./data/MME_images")
    parser.add_argument("--question-file", type=str, default="./data/MME_questions.jsonl")
    parser.add_argument("--answers-file",  type=str, required=True)
    parser.add_argument("--conv-mode",     type=str, default="llava_v1")
    parser.add_argument("--temperature",   type=float, default=1.0)
    parser.add_argument("--top_p",         type=float, default=1)
    parser.add_argument("--top_k",         type=int,   default=None)
    parser.add_argument("--noise_step",    type=int,   default=500)
    parser.add_argument("--use_cd",        action="store_true", default=False)
    parser.add_argument("--cd_alpha",      type=float, default=1.0)
    parser.add_argument("--cd_beta",       type=float, default=0.1)
    parser.add_argument("--seed",          type=int,   default=42)
    args = parser.parse_args()
    set_seed(args.seed)
    eval_model(args)
