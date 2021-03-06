#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Jun 30 20:02:01 2020

@author: suresh, erick, jessica
"""

import utils.constants as constants
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.parameter import Parameter
import numpy as np
import copy

class VisionNetwork(nn.Module):
    # reference: https://arxiv.org/pdf/2005.07648.pdf
    def __init__(self):
        super(VisionNetwork, self).__init__()
        #w,h,kernel_size,padding,stride
        w,h = self.calc_out_size(300,300,8,0,4)
        w,h = self.calc_out_size(w,h,4,0,2)
        w,h = self.calc_out_size(w,h,3,0,1)
        #moel
        self.conv_model = nn.Sequential(
            nn.Conv2d(in_channels=3, out_channels=32, kernel_size=8, stride=4), # shape: [N, 3, 299, 299]
            nn.ReLU(),
            nn.Conv2d(in_channels=32, out_channels=64, kernel_size=4, stride=2), # shape: [N, 32, 73, 73]
            nn.ReLU(),
            nn.Conv2d(in_channels=64, out_channels=64, kernel_size=3, stride=1), # shape: [N, 64, 35, 35]
            nn.ReLU(),
            SpatialSoftmax(num_rows=w, num_cols=h), # shape: [N, 64, 33, 33]
            # nn.Flatten(),
            nn.Linear(in_features=128, out_features=512), # shape: [N, 128]
            nn.ReLU(),
            nn.Linear(in_features=512, out_features=64), # shape: [N, 512]
            )
    def forward(self, x):
        x = self.conv_model(x)
        return x # shape: [N, 64]

    def calc_out_size(self,w,h,kernel_size,padding,stride):
        width = (w - kernel_size +2*padding)//stride + 1
        height = (h - kernel_size +2*padding)//stride + 1
        return width, height

class SpatialSoftmax(nn.Module):
    # reference: https://arxiv.org/pdf/1509.06113.pdf
    # https://github.com/naruya/spatial_softmax-pytorch
    # https://github.com/cbfinn/gps/blob/82fa6cc930c4392d55d2525f6b792089f1d2ccfe/python/gps/algorithm/policy_opt/tf_model_example.py#L168
    def __init__(self, num_rows, num_cols):
        super(SpatialSoftmax, self).__init__()

        self.num_rows = num_rows
        self.num_cols = num_cols

        x_map = np.empty([num_rows, num_cols], np.float32)
        y_map = np.empty([num_rows, num_cols], np.float32)

        for i in range(num_rows):
            for j in range(num_cols):
                x_map[i, j] = (i - num_rows / 2.0) / num_rows
                y_map[i, j] = (j - num_cols / 2.0) / num_cols

        self.x_map = torch.from_numpy(np.array(x_map.reshape((-1)), np.float32)).cuda() # W*H
        self.y_map = torch.from_numpy(np.array(x_map.reshape((-1)), np.float32)).cuda() # W*H

    def forward(self, x):
        x = x.view(x.shape[0], x.shape[1], x.shape[2]*x.shape[3]) # batch, C, W*H
        x = F.softmax(x, dim=2) # batch, C, W*H
        fp_x = torch.matmul(x, self.x_map) # batch, C
        fp_y = torch.matmul(x, self.y_map) # batch, C
        x = torch.cat((fp_x, fp_y), 1)
        return x # batch, C*2


class PlanRecognitionNetwork(nn.Module):
    def __init__(self):
        super(PlanRecognitionNetwork, self).__init__()
        self.in_features = constants.VISUAL_FEATURES + constants.N_DOF_ROBOT

        self.rnn_model = nn.Sequential(
            # bidirectional RNN
            #nn.RNN(input_size=self.in_features, hidden_size=2048, num_layers=2, nonlinearity='relu', bidirectional=True, batch_first=True)
            nn.LSTM(input_size=self.in_features, hidden_size=2048, num_layers=2, bidirectional=True, batch_first=True)
            ) # shape: [N, seq_len, 64+8]
        self.mean_fc = nn.Linear(in_features=4096, out_features=constants.PLAN_FEATURES) # shape: [N, seq_len, 4096]
        self.variance_fc = nn.Linear(in_features=4096, out_features=constants.PLAN_FEATURES) # shape: [N, seq_len, 4096]

    def forward(self, x):
        x, hn = self.rnn_model(x)
        x = x[:, -1] # we just need only last unit output
        mean = self.mean_fc(x)
        variance = F.softplus(self.variance_fc(x))
        return mean, variance # shape: [N, 256]


class PlanProposalNetwork(nn.Module):
    def __init__(self):
        super(PlanProposalNetwork, self).__init__()
        self.in_features = (constants.VISUAL_FEATURES + constants.N_DOF_ROBOT) + constants.VISUAL_FEATURES

        self.fc_model = nn.Sequential(
            nn.Linear(in_features=self.in_features, out_features=2048), # shape: [N, 136]
            nn.ReLU(),
            nn.Linear(in_features=2048, out_features=2048),
            nn.ReLU(),
            nn.Linear(in_features=2048, out_features=2048),
            nn.ReLU(),
            nn.Linear(in_features=2048, out_features=2048),
            nn.ReLU(),
            )
        self.mean_fc = nn.Linear(in_features=2048, out_features=constants.PLAN_FEATURES) # shape: [N, 2048]
        self.variance_fc = nn.Linear(in_features=2048, out_features=constants.PLAN_FEATURES) # shape: [N, 2048]

    def forward(self, x):
        x = self.fc_model(x)
        mean = self.mean_fc(x)
        variance = F.softplus(self.variance_fc(x))
        return mean, variance # shape: [N, 256]

class LogisticPolicyNetwork(nn.Module):
    def __init__(self, n_mix=constants.N_LOGITS):
        super(LogisticPolicyNetwork, self).__init__()
        self.in_features = (constants.VISUAL_FEATURES + constants.N_DOF_ROBOT) + constants.VISUAL_FEATURES + constants.PLAN_FEATURES

        self.n_mix = n_mix
        self.linears = []
        self.rnn_model = nn.Sequential(
            # unidirectional RNN
            nn.RNN(input_size=self.in_features, hidden_size=2048, num_layers=2, nonlinearity='relu', bidirectional=False, batch_first=True)
            ) # shape: [N, seq_len, 256 + 137]

        for i in range(self.n_mix):
            self.linears.append(nn.Linear(in_features=2048, out_features=constants.N_DOF_ROBOT)) # shape: [N, n_mix, 2048]

        self.mean_fc = nn.ModuleList(copy.deepcopy(self.linears))
        self.scale_fc = nn.ModuleList(copy.deepcopy(self.linears))
        self.logit_probs_fc = nn.ModuleList(copy.deepcopy(self.linears))

    def forward(self, x):
        x, hn = self.rnn_model(x)
        means = []
        scales = []
        logit_probs = []
        for i in range(self.n_mix):
            means.append(self.mean_fc[i](x))
            scales.append(F.softplus(self.scale_fc[i](x)))
            logit_probs.append(F.softplus(self.logit_probs_fc[i](x)))
        return torch.cat(logit_probs, 1), torch.cat(scales, 1), torch.cat(means, 1) # shape: [N, n_mix, 9] * 3
