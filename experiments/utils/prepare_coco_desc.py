import json
import os
import random

# Read 500 images from COCO val2014
img_dir = "data/coco/val2014"
images = [f for f in os.listdir(img_dir) if f.endswith(".jpg")]
random.seed(42)
sampled_images = random.sample(images, 500)

output_file = "data/coco_desc_questions.jsonl"
with open(output_file, "w") as f:
    for idx, img in enumerate(sampled_images):
        obj = {
            "question_id": idx,
            "image": img,
            "text": "Please describe this image in detail."
        }
        f.write(json.dumps(obj) + "\n")

print(f"Generated {output_file} with {len(sampled_images)} questions.")
