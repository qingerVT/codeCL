# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0



##### The dataloader for different tasks streams is implemented here.

import multiprocessing
import torch as th
from collections import defaultdict as ddict

from torch.utils.data import random_split, DataLoader
from utils.utils import *

def get_max_trg_len_by_task(task, sub_task):
    if task == 'summarize':
        max_target_length = 128
    elif task == 'translate':
        max_target_length = 256
    elif task == 'refine':
        if sub_task == 'small':
            max_target_length = 120
        else:
            max_target_length = 240
    elif task == 'concode':
        max_target_length = 150
    elif task == 'defect':
        max_target_length = 3
    return max_target_length


def get_bs(cur_task, model_tag):
    task = cur_task.split('_')[0]
    sub_task = cur_task.split('_')[-1]
    if 'codet5_small' in model_tag:
        bs = 32
        if task == 'summarize' or task == 'translate' or (task == 'refine' and sub_task == 'small'):
            bs = 64
    else:
        # codet5_base
        bs = 28
        if task == 'translate':
            bs = 25
        elif task == 'summarize':
            bs = 40
    return bs


def get_args_by_task_model(task, sub_task, model_tag):
    if task == 'translate':
        src_len = 320
        trg_len = 256
        epoch = 100
        patience = 5
        tbs = 8
        ebs = 50
    elif task == 'summarize':
        src_len = 256
        trg_len = 128
        epoch = 15
        patience = 2
        tbs = 16
        ebs = 80
    elif task == 'refine':
        if sub_task == 'small':
            src_len = 130
            trg_len = 120
            tbs = 16
            ebs = 80
        elif sub_task == 'medium':
            src_len = 240
            trg_len = 240
            tbs = 8
            ebs = 50
        epoch = 50
        patience = 5
    elif task == 'concode':
        src_len = 320
        trg_len = 150
        epoch = 30
        patience = 3
        tbs = 16
        ebs = 50
    elif task == 'defect':
        src_len = 512
        trg_len = 3
        epoch = 10
        patience = 2
        tbs = 16
        ebs = 50
    elif task == 'clone':
        src_len = 400
        trg_len = 400
        epoch = 1
        patience = 2
        tbs = 8
        ebs = 50

    # concode - train_bs=16, eval_bs=64
    # summarization - train_bs=16, eval_bs=100
    # translate - train_bs=8, eval_bs=64
    # refine-small - train_bs=16, eval_bs=128
    # refine-medium - train_bs=8, eval_bs=64
    lr = 5e-5
    if task == 'concode':
        lr = 10e-5
    elif task == 'defect':
        lr = 2e-5

    return {"ebs":ebs, "tbs":tbs, "lr":lr, "src_len":src_len, "trg_len":trg_len, "patience":patience, "epoch":epoch}


class CodeXGlueDataModule:
    def __init__(self, args, tokenizer):
        super().__init__()
        self.args = args
        self.world_size = self.args.n_gpu

        self.tokenizer = tokenizer
        self.pool = multiprocessing.Pool(self.args.cpu_cont)
        self.get_tasks_stream(args, args.stream, args.task)
        self.train_examples, self.train_data = {}, {}
        self.val_examples, self.val_data = {}, {}
        self.val_bleu_examples, self.val_bleu_data = {}, {}
        self.test_examples, self.test_data = {}, {}

    def get_tasks_stream(self, args, stream=None, taskname=None):
        self.all_tasks = []
        self.task_params = ddict(dict)
        self.filenames = ddict(dict)

        if stream == 'summarize':
            tasks = ['summarize']
            # sub_tasks = ['ruby', 'javascript', 'go', 'python', 'java', 'php']
            sub_tasks = ['java', 'php', 'javascript', 'ruby', 'python', 'go']
            self.all_tasks = [f"{tasks[0]}_{st}" for st in sub_tasks]
        elif stream == 'tasks':
            pass
        elif stream is not None:
            self.all_tasks = stream.split(',')
        elif stream is None and taskname is not None:
            sp = taskname.split('_')
            tasks, sub_tasks = [sp[0]], [sp[1]]
            self.all_tasks = [f"{tasks[0]}_{st}" for st in sub_tasks]
        else:
            raise NotImplementedError(f"Stream {stream} / Task {taskname} is not Implemented!")

        for task in self.all_tasks:
            sp = task.split('_')
            self.task_params[task] = get_args_by_task_model(sp[0], sp[1], self.args.model_name_or_path)
            self.filenames[task]['train'], self.filenames[task]['val'], self.filenames[task]['test'] = get_filenames(args.data_dir, sp[0], sp[1], '')
        pass

    def setup(self, stage = None):
        # Assign train/val/test datasets for use in dataloaders
        print("SETTING UP THE DATA")
        for curr_idx, curr_task in enumerate(self.all_tasks):
            task = curr_task.split('_')[0]
            sub_task = curr_task.split('_')[1]
            # self.args.max_source_length = self.task_params[curr_task]['src_len']
            # self.args.max_target_length = self.task_params[curr_task]['trg_len']
            self.train_filename = self.filenames[curr_task]['train']
            self.dev_filename = self.filenames[curr_task]['val']
            self.test_filename = self.filenames[curr_task]['test']

            if stage == "fit" or stage is None:
                self.train_examples[curr_task], self.train_data[curr_task] = load_and_cache_gen_data(self.args, task, sub_task, self.train_filename, self.pool, self.tokenizer, 'train', self.args.max_source_length[curr_idx], self.args.max_target_length[curr_idx], curr_task=curr_task)
                self.val_examples[curr_task], self.val_data[curr_task] = load_and_cache_gen_data(self.args, task, sub_task, self.dev_filename, self.pool, self.tokenizer, 'dev', self.args.max_source_length[curr_idx], self.args.max_target_length[curr_idx], curr_task=curr_task)

            if stage == "test" or stage is None:
                self.test_examples[curr_task], self.test_data[curr_task] = load_and_cache_gen_data(self.args, task, sub_task, self.test_filename, self.pool, self.tokenizer, 'test', self.args.max_source_length[curr_idx], self.args.max_target_length[curr_idx], only_src=False, is_sample=False, curr_task=curr_task)

        self.total_train_data_num = sum([len(ds) for ds in self.train_data])

    def get_key_prompt_init_dataloaders(self, n_prompts):
        per_task_init = n_prompts // len(self.all_tasks)
        query_key_init_dataloaders = {}
        for ti, task in enumerate(self.all_tasks):
            num_task_prompts = (n_prompts - (per_task_init * (len(self.all_tasks)-1))) if ti == 0 else per_task_init
            task_dataset = self.train_data[task]
            num_task_examples = len(task_dataset)
            assert num_task_examples >= num_task_prompts, "Not enough examples for the dataset."
            task_indices = torch.randperm(num_task_examples)[:num_task_prompts]
            task_input_ids, task_target_ids = task_dataset[task_indices]
            key_init_dataset = TensorDataset(task_input_ids, task_target_ids)
            key_init_loader = DataLoader(key_init_dataset, batch_size=self.args.eval_batch_size[ti], \
                                            sampler=torch.utils.data.SequentialSampler(key_init_dataset),\
                                            num_workers=self.args.num_workers, pin_memory=self.args.pin_memory)
            query_key_init_dataloaders[task] = key_init_loader
        return query_key_init_dataloaders

    def train_dataloader(self):
        self.train_loaders = {}
        for curr_idx, curr_task in enumerate(self.all_tasks):
            sampler = self.get_sampler(self.train_data[curr_task], dist=False, nondist='random')
            self.train_loaders[curr_task] = DataLoader(self.train_data[curr_task], batch_size=self.world_size * self.args.train_batch_size[curr_idx], \
                                            sampler=sampler, num_workers=self.args.num_workers, pin_memory=self.args.pin_memory)
        return self.train_loaders

    def val_dataloader(self):
        self.val_dataloaders = {}
        for curr_idx, curr_task in enumerate(self.all_tasks):
            sampler_ppl = self.get_sampler(self.val_data[curr_task], dist=False, nondist='sequential')
            self.val_dataloaders[curr_task] = DataLoader(self.val_data[curr_task], batch_size=self.world_size * self.args.train_batch_size[curr_idx], \
                                                sampler=sampler_ppl,num_workers=self.args.num_workers, pin_memory=self.args.pin_memory)
        return self.val_dataloaders

    def get_bleu_dataloader(self, curr_task, all_bleu=False):
        task = curr_task.split('_')[0]
        sub_task = curr_task.split('_')[0]
        curr_idx = self.all_tasks.index(curr_task)
        self.dev_filename = self.filenames[curr_task]['val']

        if all_bleu:
            bleu_samples = 1000000000
        else:
            bleu_samples = self.args.bleu_samples

        self.val_bleu_examples, self.val_bleu_data = load_and_cache_gen_data(self.args, task, sub_task, self.dev_filename, self.pool, self.tokenizer, 'dev', self.args.max_source_length[curr_idx], self.args.max_target_length[curr_idx], only_src=False, is_sample=True, bleu_samples=bleu_samples)
        sampler_bleu = self.get_sampler(self.val_bleu_data, dist=False, nondist='sequential')
        self.bleu_dataloader = DataLoader(self.val_bleu_data, batch_size=self.args.eval_batch_size[curr_idx], sampler=sampler_bleu,\
                                    num_workers=self.args.num_workers, pin_memory=False)
        return self.val_bleu_examples, self.val_bleu_data, self.bleu_dataloader

    def test_dataloader(self):
        self.test_dataloaders = {}
        for curr_idx, curr_task in enumerate(self.all_tasks):
            sampler = self.get_sampler(self.test_data[curr_task], dist=False, nondist='sequential')
            bs = self.args.eval_batch_size [0] if self.args.zeroshot else self.args.eval_batch_size[curr_idx]
            self.test_dataloaders[curr_task] =  DataLoader(self.test_data[curr_task], batch_size=bs, sampler=sampler,\
                num_workers=self.args.num_workers, pin_memory=self.args.pin_memory)
        return self.test_examples, self.test_data, self.test_dataloaders

    def get_sampler(self, data, dist=False, nondist='sequential'):
        if nondist == 'sequential':
            sampler = th.utils.data.SequentialSampler(data)
        elif nondist == 'random':
            sampler = th.utils.data.RandomSampler(data)
        else:
            raise NotImplementedError()
        if dist and self.args.local_rank != -1:
            sampler = th.utils.data.distributed.DistributedSampler(data, shuffle=False)
        return sampler