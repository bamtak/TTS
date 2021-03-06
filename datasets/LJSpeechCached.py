import os
import numpy as np
import collections
import librosa
import torch
from torch.utils.data import Dataset

from utils.text import text_to_sequence
from utils.data import (prepare_data, pad_per_step, prepare_tensor,
                        prepare_stop_target)


class MyDataset(Dataset):
    def __init__(self,
                 root_dir,
                 csv_file,
                 outputs_per_step,
                 text_cleaner,
                 ap,
                 min_seq_len=0):
        self.root_dir = root_dir
        self.wav_dir = os.path.join(root_dir, 'wavs')
        self.feat_dir = os.path.join(root_dir, 'loader_data')
        self.csv_dir = os.path.join(root_dir, csv_file)
        with open(self.csv_dir, "r", encoding="utf8") as f:
            self.frames = [line.split('|') for line in f]
        self.outputs_per_step = outputs_per_step
        self.sample_rate = ap.sample_rate
        self.cleaners = text_cleaner
        self.min_seq_len = min_seq_len
        self.items = [None] * len(self.frames)
        print(" > Reading LJSpeech from - {}".format(root_dir))
        print(" | > Number of instances : {}".format(len(self.frames)))
        self._sort_frames()

    def load_wav(self, filename):
        try:
            audio = librosa.core.load(filename, sr=self.sample_rate)
            return audio
        except RuntimeError as e:
            print(" !! Cannot read file : {}".format(filename))

    def load_np(self, filename):
        data = np.load(filename).astype('float32')
        return data

    def _sort_frames(self):
        r"""Sort sequences in ascending order"""
        lengths = np.array([len(ins[1]) for ins in self.frames])

        print(" | > Max length sequence {}".format(np.max(lengths)))
        print(" | > Min length sequence {}".format(np.min(lengths)))
        print(" | > Avg length sequence {}".format(np.mean(lengths)))

        idxs = np.argsort(lengths)
        new_frames = []
        ignored = []
        for i, idx in enumerate(idxs):
            length = lengths[idx]
            if length < self.min_seq_len:
                ignored.append(idx)
            else:
                new_frames.append(self.frames[idx])
        print(" | > {} instances are ignored by min_seq_len ({})".format(
            len(ignored), self.min_seq_len))
        self.frames = new_frames

    def __len__(self):
        return len(self.frames)

    def __getitem__(self, idx):
        if self.items[idx] is None:
            wav_name = os.path.join(self.wav_dir, self.frames[idx][0]) + '.wav'
            mel_name = os.path.join(self.feat_dir,
                                    self.frames[idx][0]) + '.mel.npy'
            linear_name = os.path.join(self.feat_dir,
                                       self.frames[idx][0]) + '.linear.npy'
            text = self.frames[idx][1]
            text = np.asarray(
                text_to_sequence(text, [self.cleaners]), dtype=np.int32)
            wav = np.asarray(self.load_wav(wav_name)[0], dtype=np.float32)
            mel = self.load_np(mel_name)
            linear = self.load_np(linear_name)
            sample = {
                'text': text,
                'wav': wav,
                'item_idx': self.frames[idx][0],
                'mel': mel,
                'linear': linear
            }
            self.items[idx] = sample
        else:
            sample = self.items[idx]
        return sample

    def collate_fn(self, batch):
        r"""
            Perform preprocessing and create a final data batch:
            1. PAD sequences with the longest sequence in the batch
            2. Convert Audio signal to Spectrograms.
            3. PAD sequences that can be divided by r.
            4. Convert Numpy to Torch tensors.
        """

        # Puts each data field into a tensor with outer dimension batch size
        if isinstance(batch[0], collections.Mapping):
            keys = list()

            wav = [d['wav'] for d in batch]
            item_idxs = [d['item_idx'] for d in batch]
            text = [d['text'] for d in batch]
            mel = [d['mel'] for d in batch]
            linear = [d['linear'] for d in batch]

            text_lenghts = np.array([len(x) for x in text])
            max_text_len = np.max(text_lenghts)
            mel_lengths = [m.shape[1] + 1 for m in mel]  # +1 for zero-frame

            # compute 'stop token' targets
            stop_targets = [
                np.array([0.] * (mel_len - 1)) for mel_len in mel_lengths
            ]

            # PAD stop targets
            stop_targets = prepare_stop_target(stop_targets,
                                               self.outputs_per_step)

            # PAD sequences with largest length of the batch
            text = prepare_data(text).astype(np.int32)
            wav = prepare_data(wav)

            # PAD features with largest length + a zero frame
            linear = prepare_tensor(linear, self.outputs_per_step)
            mel = prepare_tensor(mel, self.outputs_per_step)
            assert mel.shape[2] == linear.shape[2]
            timesteps = mel.shape[2]

            # B x T x D
            linear = linear.transpose(0, 2, 1)
            mel = mel.transpose(0, 2, 1)

            # convert things to pytorch
            text_lenghts = torch.LongTensor(text_lenghts)
            text = torch.LongTensor(text)
            linear = torch.FloatTensor(linear)
            mel = torch.FloatTensor(mel)
            mel_lengths = torch.LongTensor(mel_lengths)
            stop_targets = torch.FloatTensor(stop_targets)

            return text, text_lenghts, linear, mel, mel_lengths, stop_targets, item_idxs[
                0]

        raise TypeError(("batch must contain tensors, numbers, dicts or lists;\
                         found {}".format(type(batch[0]))))
