// Capture the MLP intermediate activations (down_proj input) per layer.
//
// The node "ffn_swiglu" holds silu(gate) * up for every layer, i.e. exactly
// the vector that down_proj consumes. Its dimension is the number of MLP
// neurons (n_ff), which is the axis the "neuron flipping" recipes operate
// on.
//
// Two corpus shapes are supported:
//   --mode plain  the file is a JSON array of strings, the mean is taken over
//                 all tokens of every prompt
//   --mode pairs  the file is a JSON array of strings read in twos: a prompt
//                 followed by the answer that is teacher forced after it. The
//                 mean is then taken over the answer tokens only, which is
//                 where the style of the generated text lives.
//
// Usage:
//   capture_ffn_neurons --model <gguf> --prompts <file.json> --out <file.bin>
//                       [--mode plain|pairs]
//
// Output layout (float32, layer major inside one item):
//   [item 0][layer 0][n_ff] [item 0][layer 1][n_ff] ...
//   [item 1][layer 0][n_ff] ...

#include "llama.h"
#include "ggml.h"
#include "ggml-backend.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <string>
#include <vector>

namespace {

struct Capture {
    std::string prefix = "ffn_swiglu";
    int n_ff = 0;
    int from_token = 0;
    std::map<int, std::vector<double>> sums;
    std::map<int, int64_t> counts;    // rows that went into the sum
    std::map<int, int64_t> seen;      // rows seen in total (global offset)
    int n_seen = 0;
};

int parse_layer_suffix(const char * name) {
    if (!name) {
        return -1;
    }
    const char * dash = std::strrchr(name, '-');
    if (!dash) {
        return -1;
    }
    return std::atoi(dash + 1);
}

bool eval_cb(struct ggml_tensor * t, bool ask, void * user_data) {
    Capture * cap = static_cast<Capture *>(user_data);
    if (!cap) {
        return true;
    }
    const char * name = ggml_get_name(t);
    if (!name) {
        return true;
    }
    if (ask) {
        return std::strncmp(name, cap->prefix.c_str(), cap->prefix.size()) == 0;
    }
    const int il = parse_layer_suffix(name);
    if (il < 0) {
        return true;
    }
    const int64_t ne0 = t->ne[0];
    const int64_t n_tok = t->ne[1] * t->ne[2];
    if (cap->n_ff == 0) {
        cap->n_ff = (int) ne0;
    }
    if (ne0 != (int64_t) cap->n_ff) {
        return true;
    }
    std::vector<float> data((size_t) ne0 * (size_t) n_tok);
    ggml_backend_tensor_get(t, data.data(), 0, data.size() * sizeof(float));

    const int64_t base = cap->seen[il];
    std::vector<double> & sum = cap->sums[il];
    if (sum.empty()) {
        sum.assign((size_t) ne0, 0.0);
    }
    int64_t used = 0;
    for (int64_t tk = 0; tk < n_tok; ++tk) {
        const int64_t global = base + tk;
        if (global < cap->from_token) {
            continue;
        }
        const float * row = data.data() + (size_t) tk * (size_t) ne0;
        for (int64_t n = 0; n < ne0; ++n) {
            sum[(size_t) n] += (double) row[n];
        }
        ++used;
    }
    cap->seen[il] = base + n_tok;
    cap->counts[il] += used;
    cap->n_seen++;
    return true;
}

// Reads a JSON array of strings. Only string contents are extracted, the
// structure around them is ignored, which is enough for the flat arrays this
// tool consumes.
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
    std::string node_prefix = "ffn_swiglu";
    std::string mode = "plain";
    int n_ctx = 2048;
    int n_gpu_layers = 999;

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
        } else if (a == "--node") {
            node_prefix = need("--node");
        } else if (a == "--mode") {
            mode = need("--mode");
        } else if (a == "--ctx-size") {
            n_ctx = std::atoi(need("--ctx-size").c_str());
        } else if (a == "--n-gpu-layers") {
            n_gpu_layers = std::atoi(need("--n-gpu-layers").c_str());
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
    cap.prefix = node_prefix;

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
    const int n_layer = llama_model_n_layer(model);
    std::vector<float> all;

    const size_t n_items = (mode == "pairs") ? items.size() / 2 : items.size();
    for (size_t pi = 0; pi < n_items; ++pi) {
        const std::string prompt_text = items[pi];
        const std::string answer_text = (mode == "pairs") ? items[pi + n_items] : std::string();

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
        cap.seen.clear();
        cap.n_seen = 0;
        cap.from_token = from_token;

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
        if (cap.n_ff == 0 || cap.sums.empty()) {
            std::fprintf(stderr, "item %zu: node '%s' not observed\n", pi, node_prefix.c_str());
            continue;
        }
        for (int il = 0; il < n_layer; ++il) {
            auto it = cap.sums.find(il);
            if (it == cap.sums.end() || cap.counts[il] == 0) {
                std::fprintf(stderr, "item %zu: layer %d missing\n", pi, il);
                continue;
            }
            const double cnt = (double) cap.counts[il];
            for (int n = 0; n < cap.n_ff; ++n) {
                all.push_back((float) (it->second[(size_t) n] / cnt));
            }
        }
        std::fprintf(stderr, "item %zu/%zu tokens=%d from=%d layers=%zu n_ff=%d\n",
                     pi + 1, n_items, (int) toks.size(), from_token, cap.sums.size(), cap.n_ff);
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
