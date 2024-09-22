# Copyright (c) 2024, School of Artificial Intelligence, OPtics and ElectroNics(iOPEN), 
# Northwestern PolyTechnical University, 
# and Institute of Artificial Intelligence (TeleAI), China Telecom.
#
# Author:   Coder.AN (an.hongjun@foxmail.com)
#           Huasen Chen (chenyifan1@mail.nwpu.edu.cn)
# 
# 
# This software is licensed under the MIT License.
# 
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# 
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# 
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

import os
import argparse
from tqdm import tqdm

import torch
from torch.utils.data import DataLoader

from mmengine.dataset import DefaultSampler

from sentence_vae.models import SentenceLLM
from sentence_vae.utils import get_tokenizer, get_config, get_dtype, load_yaml
from sentence_vae.data import TeleDSDataset, PassageCollate, SLLM_PPL


def make_parser():
    parser = argparse.ArgumentParser("SentenceVAE eval parser.")
    parser.add_argument("--server", type=str, default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("-c", "--config", type=str, required=True)
    parser.add_argument("--checkpoint", type=str, default=None)
    parser.add_argument("--device", type=str, default='cuda')
    parser.add_argument("--max_eval_samples", type=int, default=1000)
    return parser


def main(args):
    cfg = load_yaml(args.config)
    expn = os.path.splitext(os.path.basename(args.config))[0]
    svae_ref_model_cfg = get_config(cfg["svae"]["ref_model_dir"])

    model = SentenceLLM(
        svae_hidden_size=svae_ref_model_cfg.hidden_size,
        svae_vocab_size=svae_ref_model_cfg.vocab_size,
        svae_learnable_add=cfg["svae"]["learnable_add"],
        svae_load_ref_model=cfg["svae"]["load_ref_model"],
        svae_ref_model_dir=cfg["svae"]["ref_model_dir"],
        svae_ref_model_dtype=svae_ref_model_cfg.torch_dtype,
        svae_finetune_embedding=cfg["svae"]["finetune_embedding"],
        svae_word_embed_proj_dim=svae_ref_model_cfg.word_embed_proj_dim,
        svae_num_attention_heads=svae_ref_model_cfg.num_attention_heads,
        svae_num_hidden_layers=cfg["svae"]["num_hidden_layers"],
        svae_model_path=cfg["svae"]["model_path"],
        llm_ref_model_dir=cfg["llm"]["ref_model_dir"],
        llm_ref_model_dtype=svae_ref_model_cfg.torch_dtype,
        llm_finetune_layers=cfg["llm"]["finetune_layers"],
        finetune_svae=cfg["finetune_svae"],
        max_sentence_len=cfg["max_sen_len"],
        max_sentence_num=cfg["max_sen_num"],
        dropout=svae_ref_model_cfg.dropout,
        bos_id=svae_ref_model_cfg.bos_token_id,
        pad_id=svae_ref_model_cfg.pad_token_id,
        end_id=svae_ref_model_cfg.eos_token_id,
        device=torch.device(cfg["device"]),
        dtype=get_dtype(cfg["dtype"])
    )

    exp_dir = f"exp/SentenceVAE-{expn}"
    ckpt_list = os.listdir(exp_dir)
    ckpt = args.checkpoint
    if ckpt is None:
        for ckpt_path in ckpt_list:
            if "best" in ckpt_path:
                ckpt_path = os.path.join(exp_dir, ckpt_path)
                ckpt = torch.load(ckpt_path)['state_dict']
        assert ckpt is not None, f"Not found the best checkpoint under {exp_dir}."
    else:
        assert os.path.exists(ckpt), f"Checkpoint {ckpt} not found."
        ckpt = torch.load(ckpt)["state_dict"]
    model.load_state_dict(ckpt)
    model.eval()

    tokenizer = get_tokenizer(ckpt_path=cfg["svae"]["ref_model_dir"], max_seq_len=cfg["max_sen_len"])

    eval_dataset = TeleDSDataset(server_ip=args.server, server_port=args.port, eval_mode=True)
    eval_sampler = DefaultSampler(eval_dataset, shuffle=False)
    eval_collate_fn = PassageCollate(tokenizer=tokenizer, max_sentence_len=cfg["max_sen_len"], max_sentence_num=cfg["max_sen_num"], padding=True)


    eval_dataloader = DataLoader(
        dataset=eval_dataset,
        batch_size=cfg["batch_size"],
        sampler=eval_sampler,
        collate_fn=eval_collate_fn,
        num_workers=cfg["dataloader_num_workers"],
        prefetch_factor=cfg["dataloader_prefetch_factor"]
    )

    metric = SLLM_PPL()
    device = torch.device(args.device)
    for data in tqdm(eval_dataloader):
        batch_sentence_mask, batch_sentence_toks, batch_tok_mask = data 
        batch_sentence_mask = batch_sentence_mask.to(device)
        batch_sentence_toks = batch_sentence_toks.to(device)
        batch_tok_mask = batch_tok_mask.to(device)
        output = model(batch_sentence_mask, batch_sentence_toks, batch_tok_mask, mode='predict')
        metric.process(batch_sentence_mask, output)
    results = metric.compute_metrics(metric.results)
    
    print("Exp:", expn)
    print("Best PPL:", results["eval_ppl"])


if __name__ == "__main__":
    args = make_parser().parse_args()
    main(args)
