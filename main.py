import os
import pickle
import argparse
import torch
import torch.nn
import torch.utils.data
import numpy as np
from tensorboardX import SummaryWriter
from model.model import *
from preprocess import *
from utils import *

parser = argparse.ArgumentParser(description='Voice Conversion')
parser.add_argument('--ID', type=int, default=1,
                    help='Model ID')
parser.add_argument('--lsgan', type=int, default=0,
                    help='if 0 use lsgan, elif 1 use gan')
args = parser.parse_args()

ID = args.ID
LSGAN = True if args.lsgan == 0 else False
CUDA = torch.cuda.is_available()
EPOCHS = 5000
BATCH_SIZE = 1
model_dir = './model/sf1_tf2'
n_frames = 128



LR_G = 0.0002
LR_DECAY_G = LR_G / 200000
LR_D = 0.0001
LR_DECAY_D = LR_D / 200000
LAMBDA_C = 10
LAMBDA_I = 5

writer = SummaryWriter()

# Load data
with open(os.path.join(model_dir, 'coded_sps_A_norm.pkl'), 'rb') as input1:
    data_A = pickle.load(input1)

with open(os.path.join(model_dir, 'coded_sps_B_norm.pkl'), 'rb') as input2:
    data_B = pickle.load(input2)


model = CycleGAN(lsgan=LSGAN) # If use LSGAN, no sigmoid for Discriminator
if CUDA:
    model.cuda()


if CUDA:
    torch.cuda.manual_seed_all(1)
else:
    torch.manual_seed(1)


optimizer_G = torch.optim.Adam(model.G_params, lr=LR_G, betas=(0.5, 0.999))
optimizer_D = torch.optim.Adam(model.D_params, lr=LR_D, betas=(0.5, 0.999))

num_iterations = 0

def _loss(input, target, LSGAN):
    if LSGAN: # LSGAN use L2 loss and remove sigmoid from Discriminator network
        return F.mse_loss(input=input, target=target)
    else:
        return F.binary_cross_entropy(input=input, target=target)


for i in range(EPOCHS):

    dataset_A, dataset_B = sample_train_data(dataset_A = data_A, dataset_B = data_B, n_frames = n_frames)

    train_loader = torch.utils.data.DataLoader(
        ConcatDataset(
            dataset_A,
            dataset_B
        ),
        batch_size=BATCH_SIZE, shuffle=True)

    for idx, (x,y) in enumerate(train_loader):

        if num_iterations > 10000:
            LAMBDA_I = 0
        if num_iterations > 200000:
            LR_G = max(0, LR_G - LR_DECAY_G)
            LR_D = max(0, LR_D - LR_DECAY_D)

            for g in optimizer_G.param_groups:
                g['lr'] = LR_G
            for g in optimizer_D.param_groups:
                g['lr'] = LR_D


        num_iterations += 1

        optimizer_D.zero_grad()
        optimizer_G.zero_grad()

        x = x.float()
        y = y.float()

        if CUDA:
            x = x.cuda()
            y = y.cuda()

        fake_x, fake_y, cycle_x, cycle_y, x_id, y_id, d_fake_x, d_fake_y, d_real_x, d_real_y = model(x,y)


        real_label = torch.ones(d_fake_x.size())
        fake_label = torch.zeros(d_fake_x.size())

        if CUDA:
            real_label = real_label.cuda()
            fake_label = fake_label.cuda()

        # ============ Train Generators ============ #
        model.train_G()
        # GAN LOSS

        loss_G_x2y = _loss(input=d_fake_y, target=real_label, LSGAN=LSGAN)
        loss_G_y2x = _loss(input=d_fake_x, target=real_label, LSGAN=LSGAN)
        loss_gan = loss_G_x2y + loss_G_y2x

        # Cycle LOSS

        loss_cycle_x2y2x = F.l1_loss(input=cycle_x, target=x)
        loss_cycle_y2x2x = F.l1_loss(input=cycle_y, target=y)
        loss_cycle = loss_cycle_x2y2x + loss_cycle_y2x2x

        # Identity LOSS

        loss_identity_x2y = F.l1_loss(input=x_id, target=x)
        loss_identity_y2x = F.l1_loss(input=y_id, target=y)
        loss_identity = loss_identity_x2y + loss_identity_y2x

        # Total Generator Loss
        loss_total = loss_gan+ LAMBDA_C * loss_cycle + LAMBDA_I * loss_identity

        loss_total.backward(retain_graph=True)
        optimizer_G.step()

        # ============ Train Discriminators ============ #
        model.train_D()

        loss_D_fake_x = _loss(input=d_fake_x, target=fake_label, LSGAN=LSGAN)
        loss_D_fake_y = _loss(input=d_fake_y, target=fake_label, LSGAN=LSGAN)
        loss_D_real_x = _loss(input=d_real_x, target=real_label, LSGAN=LSGAN)
        loss_D_real_y = _loss(input=d_real_y, target=real_label, LSGAN=LSGAN)

        # Total Discriminator Loss
        loss_dis = loss_D_fake_x + loss_D_fake_y + loss_D_real_x + loss_D_real_y
        loss_dis.backward()
        optimizer_D.step()

        # Tensorboard
        if num_iterations % 10 == 0:
            writer.add_scalars('data/loss', {'Generator x2y': loss_G_x2y.item(),
                                             'Generator y2x': loss_G_y2x.item(),
                                             'Discriminator x': loss_D_real_x.item() + loss_D_fake_x.item(),
                                             'Discrimiantor y': loss_D_real_y.item() + loss_D_fake_y.item()},
                               num_iterations)
            writer.add_scalars('data/loss_identity', {'identity loss': loss_identity.item(),
                                                      'cycle loss': loss_cycle.item()},
                               num_iterations)


        if num_iterations % 50 == 0:
            print('Iteration: {:07d}, Generator Learning Rate: {:.7f}, Discriminator Learning Rate: {:.7f}, '
                    'Generator Loss : {:.3f}, Discriminator Loss : {:.3f}'.format(
                    num_iterations, LR_G, LR_G, loss_total.item(),
                    loss_dis.item()))

        if num_iterations % 1000 == 0:
            torch.save(model.state_dict(), os.path.join(model_dir, 'model_sf1_tf1_' + str(ID) + '.pt'))




# export scalar data to JSON for external processing
writer.export_scalars_to_json("./all_scalars_" + str(ID) + ".json")
writer.close()

torch.save(model.state_dict(), os.path.join(model_dir, 'model_sf1_tf1_' + str(ID) + '.pt'))