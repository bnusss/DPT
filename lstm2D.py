# 2D LSTM for VAN

import numpy as np
import torch
import torch.nn.functional as F
from numpy import log
from torch import nn

from utils import default_dtype_torch


class LSTM2D(nn.Module):
    def __init__(self, *args, **kwargs):
        super().__init__()
        self.L = kwargs['L']
        self.n = self.L
        self.net_depth = kwargs['net_depth']
        self.net_width = kwargs['net_width']
        self.bias = kwargs['bias']
        self.z2 = kwargs['z2']
        self.res_block = kwargs['res_block']
        self.x_hat_clip = kwargs['x_hat_clip']
        self.epsilon = kwargs['epsilon']
        self.device = kwargs['device']

        self.order = list(range(self.n))

        # model parameters
        self.rnn = nn.LSTMCell(2, self.net_width)
        self.fc1 = nn.Linear(self.net_width, 2)
        self.intermediate_h = nn.Linear(self.net_width * 2, self.net_width, bias=False)
        self.intermediate_h2 = nn.Linear(self.net_width * 2, self.net_width, bias=False)
        self.intermediate_x = nn.Linear(2, 1, bias=False)

    def _forward(self, x, h, h2):
        embedded_x = torch.stack([(x + 1) / 2, 1.0 - (x + 1) / 2], dim=1)  # (batch_size, 2)
        h_next, h_next2 = self.rnn(embedded_x, (h, h2))
        y = F.log_softmax(self.fc1(h_next), dim=1)
        return h_next, h_next2, y

    def log_prob(self, x):
        """Calculate log probability of configurations

        Args:
            sample (Tensor): shape (batch_size, L, L)

        Returns:
            log probability of each sample
        """
        log_prob = self._log_prob(x)
        if self.z2 == 1:  # Symmetry D4 for 2D lattice: rotation and reflection
            x_flip = -x
            x_rotate = torch.rot90(x, 2, [1, 2])
            x_flip_rotate = torch.rot90(x_flip, 2, [1, 2])
            log_prob2 = self._log_prob(x_flip)
            log_prob3 = self._log_prob(x_rotate)
            log_prob4 = self._log_prob(x_flip_rotate)
            log_prob = torch.logsumexp(torch.stack([log_prob, log_prob2, log_prob3, log_prob4]), dim=0)
            log_prob = log_prob - log(4)

        return log_prob

    def _log_prob(self, x):
        batch_size = x.shape[0]
        _log_prob = torch.zeros_like(x)
        mask = (1 + x) / 2  # mask is to check up or down spin-by-spin for each sample
        x_init = torch.zeros(batch_size, dtype=default_dtype_torch, device=self.device)
        h_init = torch.zeros(batch_size, self.net_width, dtype=default_dtype_torch, device=self.device)
        h_init2 = torch.zeros(batch_size, self.net_width, dtype=default_dtype_torch, device=self.device)
        h = torch.zeros(
            [batch_size, self.net_width, self.n, self.n], dtype=default_dtype_torch, device=self.device
        )
        h2 = torch.zeros(
            [batch_size, self.net_width, self.n, self.n], dtype=default_dtype_torch, device=self.device
        )

        for i in range(0, self.n):
            for j in range(0, self.n):
                if i == 0:  # Up Boundary
                    if j == 0:  # Left Boundary
                        x_inter = self.intermediate_x(torch.stack([x_init, x_init], dim=1))[:, 0]
                        h_inter = self.intermediate_h(torch.cat([h_init, h_init], dim=1))
                        h_inter2 = self.intermediate_h2(torch.cat([h_init2, h_init2], dim=1))

                    else:
                        x_inter = self.intermediate_x(torch.stack([x[:, i, j - 1], x_init], dim=1))[:, 0]
                        h_inter = self.intermediate_h(torch.cat([h[:, :, i, j - 1], h_init], dim=1))
                        h_inter2 = self.intermediate_h2(torch.cat([h2[:, :, i, j - 1], h_init2], dim=1))

                    h[:, :, i, j], h2[:, :, i, j], y = self._forward(x_inter, h_inter, h_inter2)
                    _log_prob[:, i, j] = y[:, 0] * mask[:, i, j] + y[:, 1] * (1.0 - mask[:, i, j])

                else:
                    if i % 2 == 0:  # From left to right
                        if j == 0:  # Left Boundary
                            x_inter = self.intermediate_x(torch.stack([x_init, x[:, i - 1, j]], dim=1))[:, 0]
                            h_inter = self.intermediate_h(torch.cat([h_init, h[:, :, i - 1, j]], dim=1))
                            h_inter2 = self.intermediate_h2(torch.cat([h_init2, h2[:, :, i - 1, j]], dim=1))
                        else:
                            x_inter = self.intermediate_x(
                                torch.stack([x[:, i, j - 1], x[:, i - 1, j]], dim=1)
                            )[:, 0]
                            h_inter = self.intermediate_h(
                                torch.cat([h[:, :, i, j - 1], h[:, :, i - 1, j]], dim=1)
                            )
                            h_inter2 = self.intermediate_h2(
                                torch.cat([h2[:, :, i, j - 1], h2[:, :, i - 1, j]], dim=1)
                            )
                        h[:, :, i, j], h2[:, :, i, j], y = self._forward(x_inter, h_inter, h_inter2)
                        _log_prob[:, i, j] = y[:, 0] * mask[:, i, j] + y[:, 1] * (1.0 - mask[:, i, j])

                        if i == self.n - 1 and j == self.n - 1:  # all-0 state
                            aa = torch.sum(torch.sum(mask, 2), 1)
                            Record = aa == 1  # find only-one-spin-up configuration
                            LastUp = mask[
                                Record, self.n - 1, self.n - 1
                            ]  # last spin of the only-one-spin-up configuration
                            _log_prob[Record, self.n - 1, self.n - 1] = _log_prob[
                                Record, self.n - 1, self.n - 1
                            ] * (
                                1 - LastUp
                            )  # If last spin is up, change the log_prob to 0

                    if i % 2 == 1:  # From right to left
                        jj = self.n - j - 1
                        if jj == self.n - 1:  # Rigth Boundary
                            x_inter = self.intermediate_x(torch.stack([x_init, x[:, i - 1, jj]], dim=1))[:, 0]
                            h_inter = self.intermediate_h(torch.cat([h_init, h[:, :, i - 1, jj]], dim=1))
                            h_inter2 = self.intermediate_h2(torch.cat([h_init2, h2[:, :, i - 1, jj]], dim=1))
                        else:
                            x_inter = self.intermediate_x(
                                torch.stack([x[:, i, jj + 1], x[:, i - 1, jj]], dim=1)
                            )[:, 0]
                            h_inter = self.intermediate_h(
                                torch.cat([h[:, :, i, jj + 1], h[:, :, i - 1, jj]], dim=1)
                            )
                            h_inter2 = self.intermediate_h2(
                                torch.cat([h2[:, :, i, jj + 1], h2[:, :, i - 1, jj]], dim=1)
                            )
                        h[:, :, i, jj], h2[:, :, i, jj], y = self._forward(x_inter, h_inter, h_inter2)
                        _log_prob[:, i, jj] = y[:, 0] * mask[:, i, jj] + y[:, 1] * (1.0 - mask[:, i, jj])

                        if i == self.n - 1 and j == self.n - 1:  # all-0 state
                            aa = torch.sum(torch.sum(mask, 2), 1)
                            Record = aa == 1  # find only-one-spin-up configuration
                            LastUp = mask[
                                Record, self.n - 1, 0
                            ]  # last spin of the only-one-spin-up configuration
                            _log_prob[Record, self.n - 1, 0] = _log_prob[Record, self.n - 1, 0] * (
                                1 - LastUp
                            )  # If last spin is up, change the log_prob to 0

        return _log_prob.sum(dim=2).sum(dim=1)  # _log_prob.sum(dim=1)

    def sample(self, batch_size):
        """Sample method

        Args:
            batch_size (int): batch size

        Returns:
            Samples from the LSTM model
        """
        samples = torch.zeros([batch_size, self.n, self.n], dtype=default_dtype_torch, device=self.device)
        h = torch.zeros(
            [batch_size, self.net_width, self.n, self.n], dtype=default_dtype_torch, device=self.device
        )
        h2 = torch.zeros(
            [batch_size, self.net_width, self.n, self.n], dtype=default_dtype_torch, device=self.device
        )

        x_init = torch.zeros(batch_size, dtype=default_dtype_torch, device=self.device)
        h_init = torch.zeros(batch_size, self.net_width, dtype=default_dtype_torch, device=self.device)
        h_init2 = torch.zeros(batch_size, self.net_width, dtype=default_dtype_torch, device=self.device)

        for i in range(0, self.n):
            for j in range(0, self.n):
                if i == 0:  # Up Boundary
                    if j == 0:  # Left Boundary
                        x_inter = self.intermediate_x(torch.stack([x_init, x_init], dim=1))[:, 0]
                        h_inter = self.intermediate_h(torch.cat([h_init, h_init], dim=1))
                        h_inter2 = self.intermediate_h2(torch.cat([h_init2, h_init2], dim=1))
                    else:
                        x_inter = self.intermediate_x(torch.stack([samples[:, i, j - 1], x_init], dim=1))[
                            :, 0
                        ]
                        h_inter = self.intermediate_h(torch.cat([h[:, :, i, j - 1], h_init], dim=1))
                        h_inter2 = self.intermediate_h2(torch.cat([h2[:, :, i, j - 1], h_init2], dim=1))
                    h[:, :, i, j], h2[:, :, i, j], y = self._forward(x_inter, h_inter, h_inter2)
                    p = torch.exp(y)[:, 0]
                    samples[:, i, j] = torch.bernoulli(p).to(default_dtype_torch) * 2 - 1

                else:
                    if i % 2 == 0:  # From left to right
                        if j == 0:  # Left Boundary
                            x_inter = self.intermediate_x(torch.stack([x_init, samples[:, i - 1, j]], dim=1))[
                                :, 0
                            ]
                            h_inter = self.intermediate_h(torch.cat([h_init, h[:, :, i - 1, j]], dim=1))
                            h_inter2 = self.intermediate_h2(torch.cat([h_init2, h2[:, :, i - 1, j]], dim=1))
                        else:
                            x_inter = self.intermediate_x(
                                torch.stack([samples[:, i, j - 1], samples[:, i - 1, j]], dim=1)
                            )[:, 0]
                            h_inter = self.intermediate_h(
                                torch.cat([h[:, :, i, j - 1], h[:, :, i - 1, j]], dim=1)
                            )
                            h_inter2 = self.intermediate_h2(
                                torch.cat([h2[:, :, i, j - 1], h2[:, :, i - 1, j]], dim=1)
                            )
                        h[:, :, i, j], h2[:, :, i, j], y = self._forward(x_inter, h_inter, h_inter2)
                        p = torch.exp(y)[:, 0]
                        samples[:, i, j] = torch.bernoulli(p).to(default_dtype_torch) * 2 - 1

                        if i == self.n - 1 and j == self.n - 1:  # all-0 state
                            mask = (
                                1 + samples
                            ) / 2  # mask is to check up or down spin-by-spin for each sample
                            aa = torch.sum(torch.sum(mask, 2), 1)
                            Record = aa == 0  # find all-0 configuration
                            # For the all-0 state, change the last spin to up: its probabilty has been changed accordingly in _log_prob
                            samples[Record, self.n - 1, self.n - 1] = 1

                    if i % 2 == 1:  # From right to left
                        jj = self.n - j - 1
                        if jj == self.n - 1:  # Rigth Boundary
                            x_inter = self.intermediate_x(
                                torch.stack([x_init, samples[:, i - 1, jj]], dim=1)
                            )[:, 0]
                            h_inter = self.intermediate_h(torch.cat([h_init, h[:, :, i - 1, jj]], dim=1))
                            h_inter2 = self.intermediate_h2(torch.cat([h_init2, h2[:, :, i - 1, jj]], dim=1))
                        else:
                            x_inter = self.intermediate_x(
                                torch.stack([samples[:, i, jj + 1], samples[:, i - 1, jj]], dim=1)
                            )[:, 0]
                            h_inter = self.intermediate_h(
                                torch.cat([h[:, :, i, jj + 1], h[:, :, i - 1, jj]], dim=1)
                            )
                            h_inter2 = self.intermediate_h2(
                                torch.cat([h2[:, :, i, jj + 1], h2[:, :, i - 1, jj]], dim=1)
                            )
                        h[:, :, i, jj], h2[:, :, i, jj], y = self._forward(x_inter, h_inter, h_inter2)
                        p = torch.exp(y)[:, 0]
                        samples[:, i, jj] = torch.bernoulli(p).to(default_dtype_torch) * 2 - 1

                        if i == self.n - 1 and j == self.n - 1:  # all-0 state
                            mask = (
                                1 + samples
                            ) / 2  # mask is to check up or down spin-by-spin for each sample
                            aa = torch.sum(torch.sum(mask, 2), 1)
                            Record = aa == 0  # find all-0 configuration
                            # For the all-0 state, change the last spin to up: its probabilty has been changed accordingly in _log_prob
                            samples[Record, self.n - 1, 0] = 1

        if self.z2 == 1:  # Symmetry D4 for 2D lattice: rotation and reflection
            flip = torch.randint(2, [batch_size, 1, 1], dtype=samples.dtype, device=samples.device) * 2 - 1
            samples *= flip
            rotate = torch.randint(
                2, [batch_size, 1, 1], dtype=samples.dtype, device=samples.device
            )  # 1 is rotate, 0 is not
            samples = torch.rot90(samples, 2, [1, 2]) * rotate + samples * (1 - rotate)

        x_hat = torch.zeros_like(samples)  # my code

        return samples, x_hat
