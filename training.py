import imageio
import logging
import os
import json
from timeit import default_timer
from collections import defaultdict

from tqdm import trange
import torch
from torch.nn import functional as F
from evaluate import Evaluator

import wandb

TRAIN_LOSSES_LOGFILE = "train_losses.log"
MODEL_FILENAME = "model.pt"
META_FILENAME = "specs.json"

class Trainer():

    def __init__(self, model, optimizer, scheduler = None, device=torch.device("cpu"),
                logger=logging.getLogger(__name__), metrics_freq =-2, sample_size = 64,
                save_dir ="results",
                dataset_size = 1000, all_latents = True, gif_visualizer = None, seed = None, dataset_name = None):
        self.device = device
        self.model = model.to(self.device)
        self.optimizer = optimizer
        self.scheduler = scheduler
        self.save_dir = save_dir
        self.logger = logger
        self.losses_logger = LossesLogger(os.path.join(save_dir, TRAIN_LOSSES_LOGFILE))
        self.metrics_freq =metrics_freq
        self.sample_size = sample_size
        self.dataset_size = dataset_size
        self.all_latents = all_latents
        self.gif_visualizer = gif_visualizer
        self.seed = seed
        self.dataset_name = dataset_name



    def __call__(self, data_loader, epochs=10, checkpoint_every = 10, wandb_log = False):
        start = default_timer()
        storers = []
        self.model.train()


        if wandb_log:
            train_evaluator = Evaluator(model=self.model, device=self.device, seed=self.seed,
                                        sample_size=self.sample_size, dataset_size=self.dataset_size, all_latents=self.all_latents)

        for epoch in range(epochs):
            storer = defaultdict(list)
            epoch_loss = 0

            kwargs = dict(desc="Epoch {}".format(epoch + 1), leave=False,
                      disable=False)
            with trange(len(data_loader), **kwargs) as t:

                for _, (data, _) in enumerate(data_loader):
                    batch_size, _, _, _ = data.size()
                    
                    
                    data = data.to(self.device)
                    self.optimizer.zero_grad()
                    recon_batch, mu, logvar = self.model(data)

                    loss = self.model.loss_function(recon_batch, data, mu,logvar, storer=storer)/len(data)

                    
                    loss.backward()
                    self.optimizer.step()

                    epoch_loss += loss.item()
                    t.set_postfix(loss=loss)
                    t.update()

            mean_epoch_loss = epoch_loss / len(data_loader)

            self.logger.info('Epoch: {} Average loss per image: {:.2f}'.format(epoch + 1,
                                                                               mean_epoch_loss))

            self.losses_logger.log(epoch, storer)
  
            if self.gif_visualizer is not None:
                self.gif_visualizer()

            if epoch % checkpoint_every == 0:
                save_model(self.model,self.save_dir,
                           filename="model-{}.pt".format(epoch))

            if self.scheduler is not None:
                self.scheduler.step()

            self.model.eval()

            if wandb_log:
                metrics, losses = {}, {}
                if epoch % max(round(epochs/abs(self.metrics_freq)), 10) == 0 and abs(epoch-epochs) >= 5 and (epoch != 0 if self.metrics_freq < 0 else True):
                    metrics = train_evaluator.compute_metrics(data_loader, self.dataset_name)
                losses = train_evaluator.compute_losses(data_loader, batch_size=batch_size)
                wandb.log({"epoch":epoch,"metric":metrics, "loss":losses})

            self.model.train()           

        if self.gif_visualizer is not None:
            self.gif_visualizer.save_reset()

        self.model.eval()

        delta_time = (default_timer() - start) / 60
        self.logger.info('Finished training after {:.1f} min.'.format(delta_time))





class LossesLogger(object):
    """Class definition for objects to write data to log files in a
    form which is then easy to be plotted.
    """

    def __init__(self, file_path_name):
        """ Create a logger to store information for plotting. """
        print(file_path_name)
        if os.path.isfile(file_path_name):
            os.remove(file_path_name)

        self.logger = logging.getLogger("losses_logger")
        self.logger.setLevel(1)  # always store
        file_handler = logging.FileHandler(file_path_name)
        file_handler.setLevel(1)
        self.logger.addHandler(file_handler)

        header = ",".join(["Epoch", "Loss", "Value"])
        self.logger.debug(header)

    def log(self, epoch, losses_storer):
        """Write to the log file """
        for k, v in losses_storer.items():
            log_string = ",".join(str(item) for item in [epoch, k, mean(v)])
            self.logger.debug(log_string)


def save_model(model, directory, metadata=None, filename=MODEL_FILENAME):
    """
    Save a model and corresponding metadata.

    Parameters
    ----------
    model : nn.Module
        Model.

    directory : str
        Path to the directory where to save the data.

    metadata : dict
        Metadata to save.
    """
    device = next(model.parameters()).device
    model.cpu()

    if metadata is None:
        # save the minimum required for loading
        metadata = dict(img_size=model.img_size, latent_dim=model.latent_dim,
                        model_type=model.model_type)

    path_to_metadata = os.path.join(directory, filename)

    with open(path_to_metadata, 'w') as f:
        json.dump(metadata, f, indent=4, sort_keys=True)

    path_to_model = os.path.join(directory, filename)
    torch.save(model.state_dict(), path_to_model)

    model.to(device)  # restore device

# HELPERS
def mean(l):
    """Compute the mean of a list"""
    return sum(l) / len(l)