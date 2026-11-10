import argparse, json, os
import numpy as np

ap = argparse.ArgumentParser()
ap.add_argument('--acts', required=True)
ap.add_argument('--cols', required=True)
ap.add_argument('--neurons', required=True)
ap.add_argument('--layer', type=int, required=True)
ap.add_argument('--n-layers', type=int, default=32)
ap.add_argument('--n-embd', type=int, default=4096)
ap.add_argument('--n-prompts', type=int, default=24)
ap.add_argument('--out', required=True)
ap.add_argument('--meta', default=None)
args = ap.parse_args()

neurons = [int(x) for x in args.neurons.split(',') if x.strip()]

# residual scale per layer: median norm of the residual stream leaving the layer
acts = np.fromfile(args.acts, dtype=np.float32).reshape(args.n_prompts, args.n_layers - 1, args.n_embd)
res_norm = np.linalg.norm(acts, axis=2)      # [prompt, layer]
res_med = np.median(res_norm, axis=0)         # [layer]

cols = np.fromfile(args.cols, dtype=np.float32).reshape(len(neurons), args.n_embd)

cvec = np.zeros((args.n_layers - 1, args.n_embd), dtype=np.float32)
meta = {'layer': args.layer, 'neurons': [], 'res_median': float(res_med[args.layer - 1])}
for i, nidx in enumerate(neurons):
    col = cols[i]
    norm = float(np.linalg.norm(col))
    # one activation unit of offset on this neuron
    cvec[args.layer - 1] = col
    meta['neurons'].append({
        'neuron': nidx,
        'col_norm': norm,
        # paper dose: s = dose * median(resid) / ||W_down[:, n]||
        'paper_scale_dose1': float(res_med[args.layer - 1] / max(norm, 1e-9)),
    })
    print('layer %d neuron %d col_norm=%.6f paper_scale(dose=1)=%.3f' % (
        args.layer, nidx, norm, meta['neurons'][-1]['paper_scale_dose1']))

cvec.tofile(args.out)
print('wrote', args.out, cvec.shape)
if args.meta:
    with open(args.meta, 'w', encoding='utf-8') as f:
        json.dump(meta, f, indent=1)
print('res_median layer %d = %.3f' % (args.layer, res_med[args.layer - 1]))
