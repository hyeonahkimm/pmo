#!/bin/bash 

oracle_array=('ranolazine_mpo' 'perindopril_mpo' 'amlodipine_mpo')

for seed in 4
do
for oralce in "${oracle_array[@]}"
do
# echo $oralce
CUDA_VISIBLE_DEVICES=3 python run.py reinvent_ls_gfn --config_default hparams_ls_gfn.yaml --task simple --oracle $oralce --wandb online --run_name iter --seed $seed
done
done
