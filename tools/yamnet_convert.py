"""Turn Google's YAMNet weights (yamnet.h5, Keras) into the numpy file the
analyzer's ears load (services/jb_analyzer/models/yamnet.npz). Run once, on
a development machine, when the weights change -- the cabinet never needs
h5py or TensorFlow:

    curl -O https://storage.googleapis.com/audioset/yamnet.h5
    python -m tools.yamnet_convert yamnet.h5 services/jb_analyzer/models/yamnet.npz

Each batch norm (center, no scale, epsilon 1e-4: YAMNet's params.py) is
folded into the convolution before it, so the ears only multiply and add:
    y = (conv(x) - mean) / sqrt(var + eps) + beta  ==  conv'(x) + bias'
Weights are stored as float16 (half the size; the ears compute in float32).
YAMNet is Apache 2.0 (tensorflow/models, research/audioset/yamnet); its
licence is services/jb_analyzer/models/LICENSE-yamnet.
"""

import argparse

import numpy as np

EPS = 1e-4
LAYERS = 14          # layer1 is a plain 3x3 convolution, layers 2-14 depthwise-separable


def _bn(f, name):
    mean = np.asarray(f[f"{name}/moving_mean:0"], dtype=np.float64)
    var = np.asarray(f[f"{name}/moving_variance:0"], dtype=np.float64)
    beta = np.asarray(f[f"{name}/beta:0"], dtype=np.float64)
    inv = 1 / np.sqrt(var + EPS)
    return inv, beta - mean * inv


def convert(src, dst):
    import h5py                                     # development machines only
    out = {}
    with h5py.File(src, "r") as f:
        k = np.asarray(f["layer1/conv/layer1/conv/kernel:0"], dtype=np.float64)       # (3, 3, 1, 32)
        inv, bias = _bn(f, "layer1/conv/bn/layer1/conv/bn")
        out["l1_w"], out["l1_b"] = (k[:, :, 0, :] * inv).astype(np.float16), bias.astype(np.float32)
        for n in range(2, LAYERS + 1):
            p = f"layer{n}"
            dw = np.asarray(f[f"{p}/depthwise_conv/{p}/depthwise_conv/depthwise_kernel:0"], dtype=np.float64)
            inv, bias = _bn(f, f"{p}/depthwise_conv/bn/{p}/depthwise_conv/bn")
            out[f"l{n}_dw"], out[f"l{n}_db"] = (dw[:, :, :, 0] * inv).astype(np.float16), bias.astype(np.float32)
            pw = np.asarray(f[f"{p}/pointwise_conv/{p}/pointwise_conv/kernel:0"], dtype=np.float64)
            inv, bias = _bn(f, f"{p}/pointwise_conv/bn/{p}/pointwise_conv/bn")
            out[f"l{n}_pw"], out[f"l{n}_pb"] = (pw[0, 0] * inv).astype(np.float16), bias.astype(np.float32)
        out["logits_w"] = np.asarray(f["logits/logits/kernel:0"], dtype=np.float16)
        out["logits_b"] = np.asarray(f["logits/logits/bias:0"], dtype=np.float32)
    np.savez_compressed(dst, **out)
    print(f"wrote {dst}: {sum(v.size for v in out.values()):,} numbers")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    a = ap.parse_args()
    convert(a.src, a.dst)


if __name__ == "__main__":
    main()
