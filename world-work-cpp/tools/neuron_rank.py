import json, sys
import numpy as np

bin_path = sys.argv[1]
out_path = sys.argv[2]
n_layer = 32
n_ff = 12288
n_persona = 12

data = np.fromfile(bin_path, dtype=np.float32).reshape(n_persona * 2, n_layer, n_ff)
print('loaded', data.shape)

p = data[:n_persona]
n = data[n_persona:]
mp = p.mean(axis=0)   # [layer, n_ff]
mn = n.mean(axis=0)
diff = mp - mn

sp = p.std(axis=0)
sn = n.std(axis=0)
pooled = np.sqrt((sp ** 2 + sn ** 2) / 2.0)
tstat = diff / (pooled + 1e-6)

act = np.abs(data).mean(axis=0)   # mean |activation| per neuron per layer
actmax = act.max(axis=1, keepdims=True)
rel = np.abs(diff) / (act + 1e-9)
# only neurons that actually fire carry a usable signal
active = act > 0.02 * actmax

report = {}
for L in range(n_layer):
    masked = np.where(active[L], tstat[L], -1e9)
    order = np.argsort(-masked)
    rows = []
    for nidx in order[:12]:
        rows.append({
            'neuron': int(nidx),
            'diff': float(diff[L, nidx]),
            't': float(tstat[L, nidx]),
            'mean_persona': float(mp[L, nidx]),
            'mean_neutral': float(mn[L, nidx]),
            'act_ratio': float(act[L, nidx] / max(actmax[L, 0], 1e-9)),
        })
    report[L] = rows
    top = rows[0]
    print('layer %2d  best neuron %5d  diff=%9.4f  t=%8.2f  act_ratio=%.3f  mean_p=%8.4f mean_n=%8.4f' % (
        L, top['neuron'], top['diff'], top['t'], top['act_ratio'], top['mean_persona'], top['mean_neutral']))

with open(out_path, 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=True, indent=1)
print('wrote', out_path)
