import numpy as np
import pandas as pd

import torch
import torch.optim as optim
import torch.nn as nn
from sklearn.metrics import precision_recall_fscore_support

from tqdm import tqdm
from tensorboardX import SummaryWriter

import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import sys
import time
import argparse

from model import *
from loader import *

"""###################################  init  ###################################"""
parser = argparse.ArgumentParser(description='predication model')
parser.add_argument('--model', "-m", type=str, default="HAN", help='Choose model structure')
parser.add_argument('--lr', default=1e-3, type=float, help='learning rate')
parser.add_argument('--annealing', action='store_true', help='annealing')
parser.add_argument('--batch_size', default=128, type=int, help='batch size')
parser.add_argument('--debug', action='store_true', help='debug mode with small dataset')
parser.add_argument('--resume', '-r', type=str, help='resume from checkpoint')
parser.add_argument('--init_xavier', '-i', action='store_true', help='init with xavier')
parser.add_argument('--epoch', "-e", default=10, type=int, help='max epoch')
parser.add_argument('--predict', "-p", type=str, help='list metrics of the model')
args = parser.parse_args()


def init():
    global net, model_stamp
    if args.model == "HAN":
        net = HAN(hidden_size=128,
                  attention_size=64,
                  num_classes=8)
    else:
        print("no specific model")
        sys.exit(0)

    if args.resume:
        print("loading exist model from %s" % args.resume)
        check_point = torch.load(args.resume)
        net.load_state_dict(check_point["net"])
        model_stamp = args.resume[:-4]
    else:
        t = time.localtime()
        model_stamp = "%s_%d_%.2d_%.2d" % (args.model, t.tm_mday, t.tm_hour, t.tm_min)

    if args.init_xavier:
        for name, param in net.named_parameters():
            if 'weight' in name and "embedding" not in name:
                torch.nn.init.normal_(param, -0.1, 0.1)

    net = net.cuda()

    print("initializing " + model_stamp)


"""###################################  data loader  ###################################"""


def collate(batch):
    """
    N stands for batch size,
    Notes stands for num of note columns
    W stands for num of words per sentence, it is padded along note columns for each batch
    :param batch: batch (N, 1)
    :return: X: list(Notes,), inside is sentence batch: tensor(N, W)
    :return: word_nums: list(N, ), inside is word_num: list (unpadded S, unpadded W)
    :return: y(N, C)
    """

    X, y = zip(*batch)
    # X (N, Notes), in each cell there is a list of word idx
    X = np.array(X)
    batch_size, note_size = X.shape
    word_nums = [[len(X[i, j]) for j in range(note_size)] for i in range(batch_size)]
    X = [rnn.pad_sequence([torch.Tensor(X[i, j]).cuda().long() for i in range(batch_size)], batch_first=True)
         for j in range(note_size)]
    y = torch.from_numpy(np.array(y))

    return X, word_nums, y.float()


def data_loader():
    global train_loader, val_loader, train_dataset, val_dataset, test_dataset, test_loader

    print("loading data...")
    test_dataset = Data("test")
    test_loader = DataLoader(test_dataset,
                             batch_size=args.batch_size,
                             # num_workers=1 if args.debug else 6,
                             shuffle=False,
                             collate_fn=collate)

    val_dataset = Data("validation")

    if args.debug or args.predict:
        print("loading train dataset as the small validation dataset...")
        train_dataset = val_dataset
    else:
        # train_dataset = EmbeddingData("train")
        train_dataset = Data("train")

    val_loader = DataLoader(val_dataset,
                            batch_size=args.batch_size,
                            # num_workers=1 if args.debug else 6,
                            shuffle=True,
                            collate_fn=collate)

    train_loader = DataLoader(train_dataset,
                              batch_size=args.batch_size,
                              # num_workers=1 if args.debug else 6,
                              shuffle=True,
                              collate_fn=collate)


"""###################################  train  ###################################"""


def plot_grad_flow(named_parameters, figname):
    '''Plots the gradients flowing through different layers in the net during training.
    Can be used for checking for possible gradient vanishing / exploding problems.

    Usage: Plug this function in Trainer class after loss.backwards() as
    "plot_grad_flow(self.model.named_parameters())" to visualize the gradient flow'''
    ave_grads = []
    max_grads = []
    layers = []
    for n, p in named_parameters:
        if p.requires_grad and ("bias" not in n):
            layers.append(n)
            ave_grads.append(p.grad.abs().mean())
            max_grads.append(p.grad.abs().max())
    plt.bar(np.arange(len(max_grads)), max_grads, alpha=0.1, lw=1, color="c")
    plt.bar(np.arange(len(max_grads)), ave_grads, alpha=0.1, lw=1, color="b")
    plt.hlines(0, 0, len(ave_grads) + 1, lw=2, color="k")
    plt.xticks(range(0, len(ave_grads), 1), layers, rotation="vertical")
    plt.xlim(left=0, right=len(ave_grads))
    plt.xlabel("Layers")
    plt.ylabel("average gradient")
    plt.title("Gradient flow")
    plt.grid(True)
    plt.legend([Line2D([0], [0], color="c", lw=4),
                Line2D([0], [0], color="b", lw=4),
                Line2D([0], [0], color="k", lw=4)], ['max-gradient', 'mean-gradient', 'zero-gradient'])
    plt.savefig(figname, bbox_inches='tight')
    plt.close()


def train(epoch, writer):
    global net, optimizer, criterion, train_loader
    net.train()

    running_loss = 0.0
    total_predictions = 0.0
    correct_predictions = 0.0
    acc = 0.0
    p = 0.0
    r = 0.0
    f1 = 0.0

    with tqdm(total=int(len(train_loader)), ascii=True) as pbar:
        for batch_idx, (x, seq_len, y) in enumerate(train_loader):
            y = y.cuda()
            optimizer.zero_grad()
            out, word_att_weights, note_att_weight = net(x)

            loss = criterion(out, y)
            loss.backward()

            pred = torch.zeros(out.shape).cuda()
            # TODO: other criteria?
            pred[out > 0.5] = 1.0
            total_predictions += y.shape[0] * y.shape[1]
            correct_predictions += (pred == y).sum().item()

            running_loss += loss.item()

            optimizer.step()
            torch.cuda.empty_cache()

            if batch_idx % 10 == 0:
                niter = epoch * len(train_loader) + batch_idx
                writer.add_scalar("Train Loss", loss.item(), niter)

                # metrics
                p, r, f1, _ = precision_recall_fscore_support(y.cpu().numpy(), pred.cpu().numpy())
                acc = (correct_predictions / total_predictions)
                pbar.set_postfix(curr_loss=round(loss.item(), 4),
                                 acc_avg=round(acc, 4),
                                 f1=round(np.average(f1), 4)
                                 )
                fig_freq = 10 if args.debug else 100
                if batch_idx % fig_freq == 0:
                    plot_grad_flow(net.named_parameters(),
                                   "result/%s_gf_train_e%d_b%d.png" % (model_stamp, epoch, batch_idx))
                    # print(utter_len[-1], listener_len[-1], trans_len[-1])
                    plt.imshow(note_att_weight.detach().cpu().numpy(),
                               interpolation='nearest',
                               cmap='hot')
                    # plt.xlabel("listener L%d" % (listener_len[-1]))
                    # plt.ylabel("speller L%d" % (trans_len[-1]))
                    plt.savefig("result/%s_aw_train_e%d_b%d.png" % (model_stamp, epoch, batch_idx))
                    plt.close()
                pbar.update(10 if pbar.n + 50 <= pbar.total else pbar.total - pbar.n)

    running_loss /= len(train_loader)

    return running_loss, acc, p, r, f1


def validate(loader):
    global net, optimizer, criterion
    with torch.no_grad():
        net.eval()

        running_loss = 0.0
        total_predictions = 0.0
        correct_predictions = 0.0
        preds = []

        for batch_idx, (x, seq_len, y) in enumerate(loader):
            y = y.cuda()
            out, word_att_weights, note_att_weight = net(x)

            loss = criterion(out, y).detach()

            pred = torch.zeros(out.shape).cuda()
            pred[out > 0.5] = 1.0
            preds += list(pred.cpu().numpy())
            total_predictions += y.shape[0] * y.shape[1]
            correct_predictions += (pred == y).sum().item()

            running_loss += loss.item()

        running_loss /= len(val_loader)
        acc = (correct_predictions / total_predictions)
        p, r, f1, _ = precision_recall_fscore_support(y.cpu().numpy(), pred.cpu().numpy())

        return running_loss, acc, p, r, f1, np.array(preds)


def evaluate(p, r, f1, dataset):
    metrics = get_metrics_df()
    for i in range(metrics.shape[0]):
        metrics.iloc[i] = p[i], r[i], f1[i]
    metrics.loc["micro_avg"] = np.average(np.array([p, r, f1]) * dataset.proportion, axis=1)
    metrics.loc["macro_avg"] = np.average([p, r, f1], axis=1)
    metrics = metrics.round(3)
    metrics.to_csv("result/%s_%s.csv" % (model_stamp, dataset.name))


def run_epochs():
    epoch = 0
    if args.resume:
        check_point = torch.load(args.resume)
        epoch = check_point["epoch"] + 1
    if args.predict:
        return
    elif args.debug:
        args.epoch = 1

    writer = SummaryWriter("log/%s" % model_stamp)

    if args.resume:
        train_losses = check_point["train_losses"]
        val_losses = check_point["val_losses"]
    else:
        train_losses = []
        val_losses = []

    print("start training from epoch", epoch, "-", args.epoch)
    print("statistics for epoch are average among samples and micro average among classes if possible")
    best_val_f1 = 0
    for e in range(epoch, args.epoch):
        if args.annealing:
            scheduler.step()

        train_loss, train_acc, train_p, train_r, train_f1 = train(epoch, writer)
        val_loss, val_acc, val_p, val_r, val_f1, preds = validate(val_loader)

        train_losses.append(train_loss)
        val_losses.append(val_loss)

        print("\re %3d: Train:l:%.3f|acc:%.3f|p:%.3f|r:%.3f|f1:%.3f|||Val:l:%.3f|acc:%.3f|p:%.3f|r:%.3f|f1:%.3f" %
              (e, train_loss, train_acc, *np.average([train_p, train_r, train_f1], axis=1),
               val_loss, val_acc, *np.average([val_p, val_r, val_f1], axis=1)))

        # save check point
        if not args.debug and np.average(val_f1) > best_val_f1:
            best_val_f1 = np.average(val_f1)
            # save model
            state = {'net': net.state_dict(),
                     "train_losses": train_losses,
                     "val_losses": val_losses,
                     'epoch': e,
                     }
            torch.save(state, '%s.pth' % model_stamp)
            # evaluate model
            evaluate(train_p, train_r, train_f1, dataset=train_dataset)
            evaluate(val_p, val_r, val_f1, dataset=val_dataset)
            np.save("result/%s_pred.npy" % model_stamp, np.array(preds))
    print("predicting result on test dataset...")
    test_loss, test_acc, test_p, test_r, test_f1, preds = validate(test_loader)
    print("T:l:%.3f|acc:%.3f" % (test_loss, test_acc))
    evaluate(test_p, test_r, test_f1, dataset=test_dataset)
    writer.close()


"""###################################  main  ###################################"""
if __name__ == '__main__':
    torch.multiprocessing.freeze_support()
    init()
    data_loader()  # return train and test dataset to produce prediction

    global criterion, optimizer, scheduler
    criterion = nn.BCEWithLogitsLoss()  # Binary Cross Entropy loss with Sigmoid
    # class_weight = torch.from_numpy(1 / train_dataset.proportion)
    # criterion = nn.MultiLabelSoftMarginLoss(weight=class_weight)
    optimizer = optim.Adam(net.parameters(), lr=args.lr)

    scheduler = optim.lr_scheduler.StepLR(optimizer, step_size=5, gamma=0.5)

    run_epochs()
