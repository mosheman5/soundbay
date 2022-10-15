
"""
Main training loop
--------------------
This script main.py constitutes the main training loop.
main function is wrapped with hydra @main wrapper which contains all the configuration and variables needed
to run the main training loop (models, data paths,
augmentations, preprocessing etc..) - for more details about hydra package
configuration please refer to https://hydra.cc/

The configuration files are located in ./soundbay/conf folder and it's possible to overwrite specific arguments
using the command line when running main.py (e.g. "main.py experiment.debug=True")

* prior to running this script make sure to define the data paths, annotations and output accordingly
* make sure to install all the packages stated in the requirements.txt file prior to running this script

"""

import torch
from torch.utils.data import DataLoader, WeightedRandomSampler
import wandb
from functools import partial
from pathlib import Path
from omegaconf import DictConfig, OmegaConf, ListConfig



import hydra
from hydra.utils import instantiate

import random
from unittest.mock import Mock
import os
from soundbay.utils.app import App
from soundbay.utils.logging import Logger, flatten, get_experiment_name
from soundbay.utils.checkpoint_utils import upload_experiment_to_s3
# from soundbay.utils.pydantic_validator import MyConfigSchema
from hydra.core.config_store import ConfigStore
from pydantic.dataclasses import dataclass
from pydantic import validator, BaseModel



from soundbay.trainers import Trainer



def modeling(
    trainer,
    device,
    batch_size,
    num_workers,
    train_dataset_args,
    val_dataset_args,
    optimizer_args,
    scheduler_args,
    model_args,
    logger,
    freeze_layers_for_finetune,
    equalize_data
):
    """
    modeling function takes all the variables and parameters defined in the main script
    (either through hydra configuration files or overwritten in the command line
    , instantiates them and starts a training on the relevant model chosen

    input:
    trainer - a Trainer object class instance as defined in trainers.py
    device - device (cpu\ gpu)
    batch_size - int
    num_workers - number of workers
    train_dataset_args - train dataset arguments taken from the configuration files/ overwritten
    val_dataset_args - val dataset arguments taken from the configuration files/ overwritten
    optimizer_args - optimizer  arguments taken from the configuration files/ overwritten
    scheduler_args - scheduler arguments taken from the configuration files/ overwritten
    model_args - model arguments taken from the configuration files/ overwritten
    logger - logger arguments taken from the configuration files/ overwritten
    equalize_data - Boolean argument for data equalization - given frequency of each class`

    """
    # Set paths and create dataset
    train_dataset = instantiate(train_dataset_args,_recursive_=False)
    val_dataset = instantiate(val_dataset_args, _recursive_=False)

    # Define model and device for training
    model = instantiate(model_args)
    model.to(device)

    # Assert number of labels in the dataset and the number of labels in the model
    assert model_args.num_classes == len(train_dataset.items_per_classes) == len(val_dataset.items_per_classes), \
    "Num of classes in model and the datasets must be equal, check your configs and your dataset labels!!"

    # Add model watch to WANDB
    logger.log_writer.watch(model)

    # Define dataloader for training and validation datasets as well as optimizers arguments
    if equalize_data:
        sampler = WeightedRandomSampler(train_dataset.samples_weight, len(train_dataset)) 
    else:
        sampler = None
    train_dataloader = DataLoader(
            dataset=train_dataset,
            sampler=sampler,
            shuffle=sampler is None,
            batch_size=batch_size,
            num_workers=num_workers,
            pin_memory=True,
        )
    val_dataloader = DataLoader(
            dataset=val_dataset,
            batch_size=batch_size,
            shuffle=False,
            num_workers=num_workers,
            pin_memory=True,
        )

    optimizer = instantiate(optimizer_args, model.parameters())
    scheduler = instantiate(scheduler_args, optimizer)

    # Add the rest of the parameters to trainer instance
    _trainer = trainer(
        model=model,
        train_dataloader=train_dataloader,
        val_dataloader=val_dataloader,
        optimizer=optimizer,
        scheduler=scheduler,
        logger=logger
    )

    # Freeze layers if required (optim.freeze_layers_for_finetune==True)
    if freeze_layers_for_finetune:
        model.freeze_layers()

    # Commence training

    _trainer.train()

    return

class Dataset(BaseModel):
    # label_names: ListConfig
    batch_size: int
    num_workers: int
    sample_rate: int
    data_sample_rate: int
    max_freq: int
    min_freq_filtering: int 
    n_fft: int
    hop_length: int
    train_dataset: dict
    val_dataset: dict
    class Config:
        title = "Dataset"
        # max_anystr_length = 10
        allow_mutation = False
        validate_assignment = True
        anystr_lower = False
        validate_all = True
        use_enum_values = True

    @validator("num_workers")
    def validate_num_workers(cls, num_workers:int):
        if num_workers > 4:
            raise ValueError(f"Number of works is larger than 4 {num_workers}")
        return num_workers


    @validator("train_dataset")
    def validate_train_dataset(cls, train_dataset:dict) :
        possible_train_datasets = ['soundbay.data.ClassifierDataset', 'soundbay.data.BaseDataset']
        if train_dataset['_target_'] not in possible_train_datasets: 
            raise ValueError(f"Train_dataset is not allowed from type {train_dataset['_target_']}")
        return train_dataset

class Model(BaseModel):
    criterion: dict
    model: dict

    class Config:
        title = "Model"
        # max_anystr_length = 10
        allow_mutation = False
        validate_assignment = True
        anystr_lower = True
        validate_all = True
        use_enum_values = True

    @validator("criterion")
    def validate_criterion(cls, criterion:int):
        # p = Path(path)
        possible_values = ['torch.nn.MSELoss', 'torch.nn.CrossEntropyLoss']
        if criterion['_target_'] not in possible_values:
            raise ValueError(f"'This criterion is not allowed: {criterion['_target_']}")
        return criterion


    @validator("model")
    def validate_model(cls, model:dict):
        possible_values = ['models.ResNet1Channel', 'models.GoogleResNet50withPCEN']
        if model['_target_'] not in possible_values:
            raise ValueError(f"'This model is not allowed: {model['_target_']}")
        return model

class Config(BaseModel):
    data: Dataset
    model: Model


# TODO check how to use hydra without path override
@hydra.main(config_name="main", config_path="conf", version_base='1.1')
def main(validate_args: Config) -> None:
    
    args = validate_args.copy()
    OmegaConf.resolve(validate_args)
    r_model = Config(**validate_args)
    # x_model = OmegaConf.to_object(validate_args)
    # Set logger
    _logger = wandb if not args.experiment.debug else Mock()
    experiment_name = get_experiment_name(args)
    _logger.init(project="finding_willy", name=experiment_name, group=args.experiment.group_name,
                 id=args.experiment.run_id, resume=args.experiment.checkpoint.resume)

    # Set device
    if not torch.cuda.is_available():
        print('CPU!!!!!!!!!!!')
        device = torch.device("cpu")
    else:
        print('GPU!!!!!!!!!')
        device = torch.device("cuda")

    # Convert filepaths, convenient if you wish to use relative paths
    working_dirpath = Path(hydra.utils.get_original_cwd())
    output_dirpath = Path.cwd()
    os.chdir(working_dirpath)

    # Define checkpoint
    if args.experiment.checkpoint.path:
        checkpoint = working_dirpath / args.experiment.checkpoint.path
        assert checkpoint.exists(), 'Checkpoint does not exists!'
    else:
        checkpoint = None

    # Logging
    logger = Logger(_logger, debug_mode=args.experiment.debug, artifacts_upload_limit=args.experiment.artifacts_upload_limit)
    flattenArgs = flatten(args)
    logger.log_writer.config.update(flattenArgs)
    App.init(args)

    # Define criterion
    criterion = instantiate(args.model.criterion)

    # Seed script
    if args.experiment.manual_seed is None:
        args.experiment.manual_seed = random.randint(1, 10000)
    random.seed(args.experiment.manual_seed)
    torch.manual_seed(args.experiment.manual_seed)

    # Finetune
    if args.optim.freeze_layers_for_finetune is None:
        args.optim.freeze_layers_for_finetune = False
    if args.optim.freeze_layers_for_finetune:
        print('The model is in finetune mode!')

    # instantiate Trainer class with parameters "meta" parameters
    trainer_partial = partial(
        Trainer,
        device=device,
        epochs=args.optim.epochs,
        debug=args.experiment.debug,
        criterion=criterion,
        checkpoint=checkpoint,
        output_path=output_dirpath,
        load_optimizer_state=args.experiment.checkpoint.load_optimizer_state,
        label_names=args.data.label_names,
    )
    # modeling function for training
    modeling(
        trainer=trainer_partial,
        device=device,
        batch_size=args.data.batch_size,
        num_workers=args.data.num_workers,
        train_dataset_args=args.data.train_dataset,
        val_dataset_args=args.data.val_dataset,
        optimizer_args=args.optim.optimizer,
        scheduler_args=args.optim.scheduler,
        model_args=args.model.model,
        logger=logger,
        freeze_layers_for_finetune=args.optim.freeze_layers_for_finetune,
        equalize_data=args.experiment.equalize_data
    )

    if args.experiment.bucket_name and not args.experiment.debug:
        upload_experiment_to_s3(experiment_id=logger.log_writer.run.id, dir_path=output_dirpath,
                                bucket_name=args.experiment.bucket_name, include_parent=True, logger=logger)
        

if __name__ == "__main__":
    main()
