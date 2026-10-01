"""V7 fine-tuning data (docs/FINETUNE.md, D-022).

    questions  generated questions over a training corpus, answers from XBRL facts
    rollouts   the agent (an open model) answers them; each attempt is scored and kept whole
    dataset    passing attempts -> chat-format SFT records, behind the D-022 guards

Training itself runs in finetune/train.py (Hugging Face Trainer + PEFT LoRA).
"""
