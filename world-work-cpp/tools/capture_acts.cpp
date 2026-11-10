// Capture per-layer residual-stream activations from a GGUF model.
//
// Uses the llama.cpp eval callback (llama_context_params.cb_eval) to observe
// every graph node during llama_decode. Nodes named "l_out" are the residual
// stream at the end of each layer -- the same tensor the control vector is
// added to in upstream llama_adapter_cvec::apply_to.
//
// No HF weights and no transformers are involved: this reads the same GGUF
// file the server loads.
//
// Usage:
//   capture_acts --model <gguf> --prompts <file.json> --out <file.bin>
//                [--mode plain|pairs]
//
// Corpus shapes:
//   --mode plain  the file is a JSON array of strings; the mean is taken over
//                 all tokens of every prompt.
//   --mode pairs  the file is a JSON array of strings read in two halves: the
//                 first half are prompts, the second half are the answers
//                 teacher forced after them. The mean is taken over the
//                 answer tokens only, which is where the generated style
//                 lives.
//
// Output layout (float32, layer major inside one item):
//   [item 0][layer 1][n_embd] [item 0][layer 2][n_embd] ...
//   [item 1][layer 1][n_embd] ...
// Layer 0 is dropped: upstream control vectors have no tensor for it.

#include "llama.h"
#include "ggml.h"
#include "ggml-backend.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <map>
#include <string>
#include <vector>

namespace {

struct Capture {
    int          n_embd        = 0;
    int          n_layer       = 0;
    int          from_token    = 0;
    int          total_tokens  = 0;
    std::map<int, std::vector<double>> sums;
    std::map<int, int64_t>           counts;
    bool         verbose       = false;
};

bool eval_cb(struct ggml_tensor * t, bool ask, void * user_data) {
    Capture * cap = static_cast<Capture *>(user_data);
    if (!cap) {
        return true;
    }
    if (ask) {
        const char * name = ggml_get_name(t);
        const bool wanted = name && std::strncmp(name, "l_out", 5) == 0;
        if (cap->verbose && wanted) {
            std::fprintf(stderr, "[capture] want node '%s' ne=%lld\n",
                         name, (long long) ggml_nelements(t));
        }
        return wanted;
    }
    const char * name = ggml_get_name(t);
    if (!name) {
        return true;
    }
    const char * dash = std::strrchr(name, '-');
    const int il = dash ? std::atoi(dash + 1) : -1;
    if (il < 0) {
        return true;
    }
    const int64_t ne0 = t->ne[0];
    const int64_t n_tok = t->ne[1] * t->ne[2];
    if (cap->n_embd == 0) {
        cap->n_embd = (int) ne0;
    }
    if (ne0 != (int64_t) cap->n_embd) {
        return true;
    }

    // The tensor lives in device memory on CUDA, so its data pointer is not
    // host readable. ggml_backend_tensor_get copies it back to the host.
    std::vector<float> data((size_t) ne0 * (size_t) n_tok);
    ggml_backend_tensor_get(t, data.data(), 0, data.size() * sizeof(float));

    std::vector<double> & sum = cap->sums[il];
    if (sum.empty()) {
        sum.assign((size_t) ne0, 0.0);
    }
    int64_t used = 0;
    for (int64_t row = 0; row < n_tok; ++row) {
        // Rows map to token positions when the layer was computed for every
        // token. The last layer is only computed for the final token, so its
        // single row is the last token of the sequence.
        int64_t global;
        if (n_tok == cap->total_tokens) {
            global = row;
        } else if (n_tok == 1) {
            global = cap->total_tokens - 1;
        } else {
            continue;
        }
        if (global < cap->from_token) {
            continue;
        }
        const float * v = data.data() + (size_t) row * (size_t) ne0;
        for (int64_t d = 0; d < ne0; ++d) {
            sum[(size_t) d] += (double) v[d];
        }
        ++used;
    }
    cap->counts[il] += used;
    return true;
}

bool read_string_array(const std::string & path, std::vector<std::string> & out) {
    std::ifstream pf(path, std::ios::binary);
    if (!pf) {
        return false;
    }
    std::string raw((std::istreambuf_iterator<char>(pf)), std::istreambuf_iterator<char>());
    size_t i = 0;
    while (i < raw.size()) {
        if (raw[i] != '"') {
            ++i;
            continue;
        }
        ++i;
        std::string cur;
        while (i < raw.size() && raw[i] != '"') {
            if (raw[i] == '\\' && i + 1 < raw.size()) {
                ++i;
                char c = raw[i];
                if (c == 'n') { cur.push_back('\n'); }
                else if (c == 't') { cur.push_back('\t'); }
                else if (c == 'r') { cur.push_back('\r'); }
                else if (c == 'u' && i + 4 < raw.size()) {
                    const std::string hex = raw.substr(i + 1, 4);
                    cur.push_back((char) std::strtol(hex.c_str(), nullptr, 16));
                    i += 4;
                } else {
                    cur.push_back(c);
                }
            } else {
                cur.push_back(raw[i]);
            }
            ++i;
        }
        ++i;
        out.push_back(cur);
    }
    return true;
}

} // namespace

int main(int argc, char ** argv) {
    std::string model_path;
    std::string prompts_file;
    std::string out_file;
    std::string mode = "plain";
    int n_ctx = 2048;
    int n_gpu_layers = 999;
    bool verbose = false;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto need = [&](const char * name) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "missing value for %s\n", name);
                std::exit(2);
            }
            return std::string(argv[++i]);
        };
        if (a == "--model") {
            model_path = need("--model");
        } else if (a == "--prompts") {
            prompts_file = need("--prompts");
        } else if (a == "--out") {
            out_file = need("--out");
        } else if (a == "--mode") {
            mode = need("--mode");
        } else if (a == "--ctx-size") {
            n_ctx = std::atoi(need("--ctx-size").c_str());
        } else if (a == "--n-gpu-layers") {
            n_gpu_layers = std::atoi(need("--n-gpu-layers").c_str());
        } else if (a == "--verbose") {
            verbose = true;
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", a.c_str());
            return 2;
        }
    }
    if (model_path.empty() || prompts_file.empty() || out_file.empty()) {
        std::fprintf(stderr, "--model, --prompts and --out are required\n");
        return 2;
    }

    std::vector<std::string> items;
    if (!read_string_array(prompts_file, items)) {
        std::fprintf(stderr, "cannot open prompts file\n");
        return 1;
    }
    if (items.empty()) {
        std::fprintf(stderr, "no prompts parsed from %s\n", prompts_file.c_str());
        return 1;
    }
    if (mode == "pairs" && items.size() % 2 != 0) {
        std::fprintf(stderr, "pairs mode needs an even number of strings\n");
        return 1;
    }

    llama_backend_init();

    llama_model_params mparams = llama_model_default_params();
    mparams.n_gpu_layers = n_gpu_layers;
    llama_model * model = llama_model_load_from_file(model_path.c_str(), mparams);
    if (!model) {
        std::fprintf(stderr, "failed to load model\n");
        return 1;
    }

    Capture cap;
    cap.verbose = verbose;

    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = (uint32_t) n_ctx;
    cparams.n_batch = 512;
    cparams.n_ubatch = 512;
    cparams.no_perf = true;
    cparams.cb_eval = eval_cb;
    cparams.cb_eval_user_data = &cap;

    llama_context * ctx = llama_init_from_model(model, cparams);
    if (!ctx) {
        std::fprintf(stderr, "failed to create context\n");
        llama_model_free(model);
        return 1;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model);
    cap.n_layer = llama_model_n_layer(model);
    const size_t n_items = (mode == "pairs") ? items.size() / 2 : items.size();
    std::vector<float> all;

    for (size_t pi = 0; pi < n_items; ++pi) {
        const std::string & prompt_text = items[pi];
        const std::string & answer_text = (mode == "pairs") ? items[pi + n_items] : std::string();

        std::vector<llama_token> toks;
        {
            std::vector<llama_token> buf(prompt_text.size() + 16);
            const int n = llama_tokenize(vocab, prompt_text.data(), (int) prompt_text.size(),
                                         buf.data(), (int) buf.size(), true, true);
            if (n < 0) {
                std::fprintf(stderr, "tokenize failed for item %zu: %d\n", pi, n);
                continue;
            }
            buf.resize(n);
            toks = buf;
        }
        int from_token = 0;
        if (mode == "pairs" && !answer_text.empty()) {
            std::vector<llama_token> buf(answer_text.size() + 16);
            const int n = llama_tokenize(vocab, answer_text.data(), (int) answer_text.size(),
                                         buf.data(), (int) buf.size(), false, false);
            if (n < 0) {
                std::fprintf(stderr, "tokenize failed for answer %zu: %d\n", pi, n);
                continue;
            }
            buf.resize(n);
            from_token = (int) toks.size();
            toks.insert(toks.end(), buf.begin(), buf.end());
        }

        cap.sums.clear();
        cap.counts.clear();
        cap.from_token = from_token;
        cap.total_tokens = (int) toks.size();

        llama_memory_clear(llama_get_memory(ctx), true);

        llama_batch batch = llama_batch_init((int) toks.size(), 0, 1);
        batch.n_tokens = (int32_t) toks.size();
        for (size_t t = 0; t < toks.size(); ++t) {
            batch.token[t] = toks[t];
            batch.pos[t] = (llama_pos) t;
            batch.n_seq_id[t] = 1;
            batch.seq_id[t][0] = 0;
            batch.logits[t] = (t + 1 == toks.size()) ? 1 : 0;
        }
        const int rc = llama_decode(ctx, batch);
        llama_batch_free(batch);
        if (rc != 0) {
            std::fprintf(stderr, "decode failed for item %zu: %d\n", pi, rc);
            continue;
        }
        if (cap.sums.empty()) {
            std::fprintf(stderr, "item %zu: no l_out node observed\n", pi);
            continue;
        }
        for (int il = 1; il < cap.n_layer; ++il) {
            auto it = cap.sums.find(il);
            if (it == cap.sums.end() || cap.counts[il] == 0) {
                std::fprintf(stderr, "item %zu: layer %d missing\n", pi, il);
                continue;
            }
            const double cnt = (double) cap.counts[il];
            for (int d = 0; d < cap.n_embd; ++d) {
                all.push_back((float) (it->second[(size_t) d] / cnt));
            }
        }
        std::fprintf(stderr, "item %zu/%zu tokens=%d from=%d layers=%zu\n",
                     pi + 1, n_items, (int) toks.size(), from_token, cap.sums.size());
    }

    std::ofstream of(out_file, std::ios::binary);
    if (!of) {
        std::fprintf(stderr, "cannot write output file\n");
        llama_free(ctx);
        llama_model_free(model);
        return 1;
    }
    of.write(reinterpret_cast<const char *>(all.data()),
             (std::streamsize) (all.size() * sizeof(float)));
    of.close();

    std::fprintf(stderr, "wrote %zu floats to %s\n", all.size(), out_file.c_str());

    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();
    return 0;
}
