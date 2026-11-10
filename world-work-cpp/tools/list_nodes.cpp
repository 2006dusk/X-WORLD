// List every graph node name and shape observed during one decode.
//
// Uses the same llama_context_params.cb_eval hook as capture_acts, but in
// "ask" mode only: the callback prints what it sees and never asks the
// scheduler to keep a tensor, so no extra device memory is used.
//
// Usage:
//   list_nodes --model <gguf> [--prompt <text>] [--ctx-size N]

#include "llama.h"
#include "ggml.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>
#include <set>

namespace {

struct Seen {
    std::set<std::string> names;
    bool verbose = false;
};

bool eval_cb(struct ggml_tensor * t, bool ask, void * user_data) {
    Seen * seen = static_cast<Seen *>(user_data);
    if (!seen) {
        return true;
    }
    const char * name = ggml_get_name(t);
    if (!name) {
        name = "?";
    }
    if (ask) {
        char key[512];
        std::snprintf(key, sizeof(key), "%s|%d|%lld|%lld|%lld",
                      name,
                      (int) t->type,
                      (long long) t->ne[0], (long long) t->ne[1], (long long) t->ne[2]);
        if (seen->names.insert(key).second) {
            std::fprintf(stderr, "NODE %s type=%d ne=%lld,%lld,%lld nelem=%lld\n",
                         name,
                         (int) t->type,
                         (long long) t->ne[0], (long long) t->ne[1], (long long) t->ne[2],
                         (long long) ggml_nelements(t));
        }
        // never request retention in this tool
        return false;
    }
    return true;
}

} // namespace

int main(int argc, char ** argv) {
    std::string model_path;
    std::string prompt = "Tell me about the weather.";
    int n_ctx = 1024;

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
        } else if (a == "--prompt") {
            prompt = need("--prompt");
        } else if (a == "--ctx-size") {
            n_ctx = std::atoi(need("--ctx-size").c_str());
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", a.c_str());
            return 2;
        }
    }
    if (model_path.empty()) {
        std::fprintf(stderr, "--model is required\n");
        return 2;
    }

    llama_backend_init();

    llama_model_params mparams = llama_model_default_params();
    mparams.n_gpu_layers = 0;
    llama_model * model = llama_model_load_from_file(model_path.c_str(), mparams);
    if (!model) {
        std::fprintf(stderr, "failed to load model\n");
        return 1;
    }

    Seen seen;
    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = (uint32_t) n_ctx;
    cparams.n_batch = 128;
    cparams.n_ubatch = 128;
    cparams.no_perf = true;
    cparams.cb_eval = eval_cb;
    cparams.cb_eval_user_data = &seen;

    llama_context * ctx = llama_init_from_model(model, cparams);
    if (!ctx) {
        std::fprintf(stderr, "failed to create context\n");
        llama_model_free(model);
        return 1;
    }

    const llama_vocab * vocab = llama_model_get_vocab(model);
    std::vector<llama_token> toks(prompt.size() + 16);
    const int n = llama_tokenize(vocab, prompt.data(), (int) prompt.size(), toks.data(), (int) toks.size(), true, true);
    if (n < 0) {
        std::fprintf(stderr, "tokenize failed\n");
        llama_free(ctx);
        llama_model_free(model);
        return 1;
    }
    toks.resize(n);

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
        std::fprintf(stderr, "decode failed: %d\n", rc);
    }

    std::fprintf(stderr, "unique node signatures: %zu\n", seen.names.size());
    llama_free(ctx);
    llama_model_free(model);
    llama_backend_free();
    return 0;
}
