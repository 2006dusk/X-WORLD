import json, os, subprocess, sys
import os
import sys
import numpy as np
# Repo root, derived from this file so the script carries no local path.
REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))

ROOT = os.path.join(REPO, 'world-work-cpp')
DATA = os.path.join(REPO, "..", 'docs', '理论支持', 'data', 'neuron')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from local_paths import model_path

MODEL = model_path()
DUMP = os.path.join(ROOT, 'build', 'dump_ffn_weights.exe')

n_items = 8
n_layers_captured = 31      # layer 31 is not captured (only the last token)
n_ff = 12288
n_embd = 4096
n_model_layers = 32
top_k = int(sys.argv[1]) if len(sys.argv) > 1 else 4
out_name = sys.argv[2] if len(sys.argv) > 2 else 'style_cvec.f32'
pos_name = sys.argv[3] if len(sys.argv) > 3 else 'style_persona.bin'
neg_name = sys.argv[4] if len(sys.argv) > 4 else 'style_neutral.bin'
prefix = sys.argv[5] if len(sys.argv) > 5 else 'style'

p = np.fromfile(os.path.join(DATA, pos_name), dtype=np.float32)
n = np.fromfile(os.path.join(DATA, neg_name), dtype=np.float32)
assert p.size == n.size, (p.size, n.size)
shape = (n_items, n_layers_captured, n_ff)
P = p.reshape(shape)
N = n.reshape(shape)

mp = P.mean(axis=0)
mn = N.mean(axis=0)
diff = mp - mn
sp = P.std(axis=0)
sn = N.std(axis=0)
pooled = np.sqrt((sp ** 2 + sn ** 2) / 2.0)
tstat = diff / (pooled + 1e-6)
act = np.abs(np.concatenate([P, N], axis=0)).mean(axis=0)
actmax = act.max(axis=1, keepdims=True)

selection = {}
for L in range(1, n_layers_captured):
    mask = act[L] > 0.05 * actmax[L, 0]
    score = np.where(mask, tstat[L], -1e9)
    order = np.argsort(-score)[:top_k]
    selection[L] = [int(x) for x in order]
    print('layer %2d neurons %s  diff %s' % (
        L, selection[L], ['%.3f' % diff[L, x] for x in order]))

# neuron-space difference of means, mapped back to the residual stream
cvec = np.zeros((n_model_layers - 1, n_embd), dtype=np.float32)
per_layer = {}
for L in range(1, n_layers_captured):
    neurons = selection[L]
    cols_path = os.path.join(DATA, '%s_cols_L%d.f32' % (prefix, L))
    norms_path = os.path.join(DATA, '%s_norms_L%d.f32' % (prefix, L))
    if not os.path.exists(cols_path):
        cmd = [DUMP, '--model', MODEL, '--layer', str(L),
               '--neurons', ','.join(str(x) for x in neurons),
               '--out-norms', norms_path, '--out-cols', cols_path]
        subprocess.run(cmd, check=True, capture_output=True)
    cols = np.fromfile(cols_path, dtype=np.float32).reshape(len(neurons), n_embd)
    coef = diff[L, neurons]
    vec = (cols * coef[:, None]).sum(axis=0)
    cvec[L - 1] = vec
    per_layer[L] = {'neurons': neurons, 'diff': [float(x) for x in coef],
                    'norm': float(np.linalg.norm(vec))}
    print('layer %2d vector norm %.4f  (neurons %s)' % (L, per_layer[L]['norm'], neurons))

out_path = os.path.join(DATA, out_name)
cvec.tofile(out_path)
print('wrote', out_path, cvec.shape)
with open(os.path.join(DATA, out_name + '.json'), 'w', encoding='utf-8') as f:
    json.dump(per_layer, f, indent=1)
