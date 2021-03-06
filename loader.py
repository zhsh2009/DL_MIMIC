import os

import torch
from torch.utils.data.dataset import Dataset
from torch.utils.data import DataLoader
import torch.nn.utils.rnn as rnn

import pandas as pd
import numpy as np

from tqdm import tqdm

EMBEDDING_DIM = 256


class Data(Dataset):
    def __init__(self, dataset):
        self.name = dataset

        if dataset == "train":
            x_path = "train_idx.pkl"
            y_path = "train_label.csv"
        elif dataset == "validation":
            x_path = "val_idx.pkl"
            y_path = "val_label.csv"
        else:
            x_path = "test_idx.pkl"
            y_path = "test_label.csv"

        X = pd.read_pickle(os.path.join(check_sys_path(), x_path)).values
        y = pd.read_csv(os.path.join(check_sys_path(), y_path)).values
        self.proportion = np.sum(y, axis=0) / np.sum(y)

        self.X = []
        self.y = []
        for i, x in enumerate(X):
            if x.shape[0] > 0:
                self.X.append(x)
                self.y.append(y[i])
    @staticmethod
    def get_vacab_size():
        with open(os.path.join(check_sys_path(), "word2idx.txt")) as f:
            vocab_size = len(f.readlines())
        return vocab_size

    def __getitem__(self, idx):
        return self.X[idx], self.y[idx]

    def __len__(self):
        return len(self.X)


def check_sys_path():
    """
    :return: absolute path of the folder to store data
    """
    cwd = os.getcwd()
    if "jeffy" in cwd.lower():  # local env
        return "C:\\Users\\Jeffy\\Downloads\\Data\\project"
    else:  # aws env
        return "data"


def get_metrics_df():
    df = pd.DataFrame({"p": [-1] * 8, "r": [-1] * 8, "f1": [-1] * 8})
    with open(os.path.join(check_sys_path(), "med2idx.txt")) as f:
        medicines = [line.split(":")[0] for line in f]
    df.index = medicines
    return df


word2idx = dict()
with open(os.path.join(check_sys_path(), "word2idx.txt")) as f:
    for line in f:
        word, idx = line.strip().split(":")
        word2idx[word] = int(idx)

if __name__ == '__main__':
    pass
