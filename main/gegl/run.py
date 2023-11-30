import os
path_here = os.path.dirname(os.path.realpath(__file__))

import random
import numpy as np
import torch
from torch.utils.data import DataLoader
from torch.optim import Adam
from joblib import Parallel

from main.optimizer import BaseOptimizer
from runner.gegl_trainer import GeneticExpertGuidedLearningTrainer
from runner.guacamol_generator import GeneticExpertGuidedLearningGenerator
from model.neural_apprentice import SmilesGenerator, SmilesGeneratorHandler
from model.genetic_expert import GeneticOperatorHandler
from util.storage.priority_queue import MaxRewardPriorityQueue
from util.storage.recorder import Recorder
from util.chemistry.benchmarks import load_benchmark
from util.smiles.char_dict import SmilesCharDictionary


class GEGL_Optimizer(BaseOptimizer):

    def __init__(self, args=None):
        super().__init__(args)
        self.model_name = "gegl"

    def _optimize(self, oracle, config):

        self.oracle.assign_evaluator(oracle)

        device = torch.device(0)

        char_dict = SmilesCharDictionary(dataset=config['dataset'], max_smi_len=config['max_smiles_length'])

        # Prepare max-reward priority queues
        apprentice_storage = MaxRewardPriorityQueue()
        expert_storage = MaxRewardPriorityQueue()

        # Prepare neural apprentice (we use the weights pretrained on existing dataset)
        apprentice = SmilesGenerator.load(load_dir=config['apprentice_load_dir'])
        apprentice = apprentice.to(device)
        apprentice_optimizer = Adam(apprentice.parameters(), lr=config['learning_rate'])
        apprentice_handler = SmilesGeneratorHandler(
            model=apprentice,
            optimizer=apprentice_optimizer,
            char_dict=char_dict,
            max_sampling_batch_size=config['max_sampling_batch_size'],
        )
        apprentice.train()

        # Prepare genetic expert
        expert_handler = GeneticOperatorHandler(mutation_rate=config['mutation_rate'])

        # Prepare trainer that collect samples from the models & optimize the neural apprentice
        # trainer = GeneticExpertGuidedLearningTrainer(
        #     apprentice_storage=apprentice_storage,
        #     expert_storage=expert_storage,
        #     apprentice_handler=apprentice_handler,
        #     expert_handler=expert_handler,
        #     char_dict=char_dict,
        #     num_keep=config['num_keep'],
        #     apprentice_sampling_batch_size=config['apprentice_sampling_batch_size'],
        #     expert_sampling_batch_size=config['expert_sampling_batch_size'],
        #     apprentice_training_batch_size=config['apprentice_training_batch_size'],
        #     num_apprentice_training_steps=config['num_apprentice_training_steps'],
        #     init_smis=[],
        # )

        pool = Parallel(n_jobs=config['num_jobs'])

        step = 0
        patience = 0

        while True:
            if len(self.oracle) > 100:
                self.sort_buffer()
                old_scores = [item[1][0] for item in list(self.mol_buffer.items())[:100]]
            else:
                old_scores = 0

            # update_storage_by_apprentice
            with torch.no_grad():
                apprentice_handler.model.eval()
                smis, _, _, _ = apprentice_handler.sample(
                    num_samples=config['apprentice_sampling_batch_size'], device=device
                )
            
            smis = list(set(smis))
            k = min((self.oracle.max_oracle_calls - len(self.oracle)), len(smis))
            smis = smis[:k]

            score = np.array(self.oracle(smis))

            if self.finish:
                print('max oracle hit')
                break
            
            apprentice_storage.add_list(smis=smis, scores=score)
            apprentice_storage.squeeze_by_kth(k=config['num_keep'])

            # update_storage_by_expert
            expert_smis, expert_scores = apprentice_storage.sample_batch(
                config['expert_sampling_batch_size']
            )
            smis = expert_handler.query(
                query_size=config['expert_sampling_batch_size'], mating_pool=expert_smis, pool=pool
            )

            smis = list(set(smis))  # 
            k = min((self.oracle.max_oracle_calls - len(self.oracle)), len(smis))
            smis = smis[:k]

            score = np.array(self.oracle(smis))

            if self.finish:
                print('max oracle hit')
                break

            expert_storage.add_list(smis=smis, scores=score)
            expert_storage.squeeze_by_kth(k=config['num_keep'])

            # train_apprentice_step
            avg_loss = 0.0
            apprentice_smis, _ = apprentice_storage.get_elems()
            expert_smis, _ = expert_storage.get_elems()
            total_smis = list(set(apprentice_smis + expert_smis))

            apprentice_handler.model.train()
            for _ in range(config['num_apprentice_training_steps']):
                smis = random.choices(population=total_smis, k=config['apprentice_training_batch_size'])
                loss = apprentice_handler.train_on_batch(smis=smis, device=device)

                avg_loss += loss / config['num_apprentice_training_steps']

            fit_size = len(total_smis)


