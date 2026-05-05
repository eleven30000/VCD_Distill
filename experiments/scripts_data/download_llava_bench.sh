#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

mkdir -p data/llava-bench-in-the-wild
cd data/llava-bench-in-the-wild

echo ">>> Downloading LLaVA-Bench (in-the-wild)..."
wget -qnc https://huggingface.co/datasets/liuhaotian/llava-bench-in-the-wild/resolve/main/questions.jsonl
wget -qnc https://huggingface.co/datasets/liuhaotian/llava-bench-in-the-wild/resolve/main/images.zip

echo ">>> Unzipping images..."
unzip -qo images.zip
# usually extracts to a folder 'images/', if not we will fix it later.
echo ">>> Done."
