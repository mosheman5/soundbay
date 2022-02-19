from typing import Union, Generator, Tuple
import torch
import torch.utils.data
from tqdm import tqdm
from pathlib import Path
from soundbay.utils.app import app
from soundbay.utils.logging import Logger
import numpy as np
import time
import matplotlib.pyplot as plt
import wandb


class Trainer:
    """
    Trainer class -
    concludes the variables related to training a model
    Args:
        train_dataloader(Generator) - defined in main.py
        val_dataloader(Generator) - defined in main.py
        optimizer(torch.optimizer) -
        criterion(torch.o)
        epochs: int,
        logger: Logger,
        output_path: Union[str, Path],
        device: Union[torch.device, None] = torch.device("cpu"),
        scheduler=None,
        checkpoint: str = None,
        debug: bool = False):

    """
    def __init__(self,
                 model: torch.nn.Module,
                 train_dataloader: Generator[Tuple[torch.tensor, torch.tensor], None, None],
                 val_dataloader: Generator[Tuple[torch.tensor, torch.tensor], None, None],
                 optimizer: torch.optim,
                 criterion,
                 epochs: int,
                 logger: Logger,
                 output_path: Union[str, Path],
                 device: Union[torch.device, None] = torch.device("cpu"),
                 scheduler=None,
                 checkpoint: str = None,
                 debug: bool = False):

        # set parameters for stft loss
        self.model = model
        self.epochs = epochs
        self.logger = logger
        self.criterion = criterion
        self.device = device
        self.epochs_trained = 0
        self.debug = debug
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.verbose = True
        self.train_dataloader = train_dataloader
        self.val_dataloader = val_dataloader
        self.output_path = output_path

        # load checkpoint
        if checkpoint:
            self._load_checkpoint(checkpoint_path=checkpoint)

    def train(self):
        best_loss = float('inf')
        # Run training script
        iterator = tqdm(range(self.epochs_trained, self.epochs),
                        desc='Running Epochs', leave=True, disable=not self.verbose)
        for epoch in iterator:
            self.logger.reset_losses()
            self.train_epoch(epoch)
            self.epochs_trained += 1
            self.eval_epoch(epoch)
            # save checkpoint
            loss = self.logger.loss_meter_val['loss'].summarize_epoch()
            if loss < best_loss:
                best_loss = loss
                self._save_checkpoint("best.pth")
            self._save_checkpoint("last.pth")
            if self.verbose:  # show batch metrics in progress bar
                s = 'epoch: ' + str(epoch) + ', ' + str(self.logger.metrics_dict)
                iterator.set_postfix_str(s)
            if self.debug and epoch > 2:
                break
        # wandb.run.log({"artifacts_table" : self.train_dataloader.dataset.artifacts_table})
        # time.sleep(10)
        # wandb.run.finish()

    def train_epoch(self, epoch):
        self.model.train()
        for it, batch in tqdm(enumerate(self.train_dataloader), desc='train'):
            if it == 2:
                break

            self.model.zero_grad()
            audio, label, raw_wav, idx = batch
            audio, label, raw_wav, idx  = audio.to(self.device), label.to(self.device), raw_wav.to(self.device), idx.to(self.device)

            if it == 1:
                self.logger.upload_artifacts(audio, label, raw_wav, idx, sample_rate=self.train_dataloader.dataset.sample_rate, flag='train', epoch=epoch)
                break
            # estimate and calc losses
            estimated_label = self.model(audio)
            loss = self.criterion(estimated_label, label)
            loss.backward()
            self.optimizer.step()

            # update losses and log batch
            # TODO: add logging of batch to logger

            self.logger.update_losses(loss.detach(), flag='train')
            self.logger.update_predictions((estimated_label, label))

        # logging
        if not app.args.experiment.debug:
            self.logger.calc_metrics(epoch, 'train')
            # wandb.run.log({"artifacts_table" :self.train_dataloader.dataset.artifacts_table}) 

        self.logger.log(epoch, 'train')
        self.train_dataloader.artifacts_logger = []
        if self.scheduler is not None:
            self.scheduler.step()

    def eval_epoch(self, epoch: int):

        with torch.no_grad():
            self.model.eval()
            for it, batch in tqdm(enumerate(self.val_dataloader), desc='val'):
                if it == 1:
                    break
                audio, label = batch
                audio, label = audio.to(self.device), label.to(self.device)

                # estimate and calc losses
                estimated_label = self.model(audio)
                loss = self.criterion(estimated_label, label)

                # update losses
                self.logger.update_losses(loss.detach(), flag='val')
                self.logger.update_predictions((estimated_label, label))

            # logging
            if not app.args.experiment.debug:
                self.logger.calc_metrics(epoch ,'val')
            self.logger.log(epoch, 'val')


    def _save_checkpoint(self, checkpoint_path: Union[str, None]):
        """Save checkpoint.
        Args:
            checkpoint_path (str): Checkpoint path to be saved.
        """
        if checkpoint_path is None or app.args.experiment.debug:
            return
        state_dict = {"optimizer": self.optimizer.state_dict(),
                      "scheduler": self.scheduler.state_dict() if self.scheduler is not None else None,
                      "epochs": self.epochs_trained,
                      "model": self.model.state_dict(),
                      "args": app.args
                      }

        torch.save(state_dict, self.output_path / checkpoint_path)

    def _load_checkpoint(self, checkpoint_path: Union[str, None]):
        """Load checkpoint.
        Args:
            checkpoint_path (str): Checkpoint path to be loaded.
        """
        if checkpoint_path is None:
            return
        print('Loading checkpoint')
        state_dict = torch.load(checkpoint_path, map_location='cpu')
        self.epochs_trained = state_dict["epochs"]
        self.optimizer.load_state_dict(state_dict["optimizer"])
        if self.scheduler is not None:
            self.scheduler.load_state_dict(state_dict["scheduler"])
        self.model.load_state_dict(state_dict["model"])
