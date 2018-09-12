
        #################################################
        ### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
        #################################################
        # file to edit: 002b_augment_training.ipynb

from nb_002 import *

import typing
from typing import Dict, Any, AnyStr, List, Sequence, TypeVar, Tuple, Optional, Union

def normalize(x, mean,std):   return (x-mean[...,None,None]) / std[...,None,None]
def denormalize(x, mean,std): return x*std[...,None,None] + mean[...,None,None]

def normalize_batch(b, mean, std, do_y=False):
    x,y = b
    x = normalize(x,mean,std)
    if do_y: y = normalize(y,mean,std)
    return x,y

def normalize_funcs(mean, std, do_y=False, device=None):
    if device is None: device=default_device
    return (partial(normalize_batch, mean=mean.to(device),std=std.to(device)),
            partial(denormalize,     mean=mean,           std=std))

@dataclass
class DeviceDataLoader():
    dl: DataLoader
    device: torch.device
    tfms: List[Callable]=None
    collate_fn: Callable=data_collate
    def __post_init__(self):
        self.dl.collate_fn=self.collate_fn
        self.tfms = listify(self.tfms)

    def __len__(self): return len(self.dl)

    def add_tfm(self,tfm):    self.tfms.append(tfm)
    def remove_tfm(self,tfm): self.tfms.remove(tfm)

    def proc_batch(self,b):
        b = to_device(b, self.device)
        for f in listify(self.tfms): b = f(b)
        return b

    def __iter__(self):
        self.gen = map(self.proc_batch, self.dl)
        return iter(self.gen)

    @classmethod
    def create(cls, dataset, bs=1, shuffle=False, device=default_device, tfms=tfms, collate_fn=data_collate, **kwargs):
        return cls(DataLoader(dataset, batch_size=bs, shuffle=shuffle, **kwargs),
                   device=device, tfms=tfms, collate_fn=collate_fn)

@dataclass
class DataBunch():
    train_dl:DataLoader
    valid_dl:DataLoader
    device:torch.device=None
    def __post_init__(self):
        if self.device is None: self.device=default_device

    @classmethod
    def create(cls, train_ds, valid_ds, bs=64, train_tfm=None, valid_tfm=None, device=None, tfms=None,
               num_workers=4, **kwargs):
        if train_tfm: train_ds = DatasetTfm(train_ds,train_tfm, **kwargs)
        if valid_tfm: valid_ds = DatasetTfm(valid_ds,valid_tfm, **kwargs)
        return cls(DeviceDataLoader.create(train_ds, bs,   shuffle=True,  device=device, tfms=tfms, num_workers=num_workers),
                   DeviceDataLoader.create(valid_ds, bs*2, shuffle=False, device=device, tfms=tfms, num_workers=num_workers),
                   device=device)

    @property
    def train_ds(self): return self.train_dl.dl.dataset
    @property
    def valid_ds(self): return self.valid_dl.dl.dataset
    @property
    def c(self): return self.train_ds.c

def conv_layer(ni, nf, ks=3, stride=1):
    return nn.Sequential(
        nn.Conv2d(ni, nf, kernel_size=ks, bias=False, stride=stride, padding=ks//2),
        nn.BatchNorm2d(nf),
        nn.LeakyReLU(negative_slope=0.1, inplace=True))

class ResLayer(nn.Module):
    def __init__(self, ni):
        super().__init__()
        self.conv1=conv_layer(ni, ni//2, ks=1)
        self.conv2=conv_layer(ni//2, ni, ks=3)

    def forward(self, x): return x + self.conv2(self.conv1(x))

class Darknet(nn.Module):
    def make_group_layer(self, ch_in, num_blocks, stride=1):
        return [conv_layer(ch_in, ch_in*2,stride=stride)
               ] + [(ResLayer(ch_in*2)) for i in range(num_blocks)]

    def __init__(self, num_blocks, num_classes, nf=32):
        super().__init__()
        layers = [conv_layer(3, nf, ks=3, stride=1)]
        for i,nb in enumerate(num_blocks):
            layers += self.make_group_layer(nf, nb, stride=2-(i==1))
            nf *= 2
        layers += [nn.AdaptiveAvgPool2d(1), Flatten(), nn.Linear(nf, num_classes)]
        self.layers = nn.Sequential(*layers)

    def forward(self, x): return self.layers(x)