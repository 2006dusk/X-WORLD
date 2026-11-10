// world-work-cpp: a self written llama.cpp compatible inference server.
//
// Usage:
//   world-work-cpp --model <path.gguf> [--host 127.0.0.1] [--port 8080]
//                  [--ctx-size 16384] [--n-gpu-layers 999] [--parallel 1]
//                  [--cache-type-k q8_0] [--cache-type-v q8_0] [--flash-attn]
//                  [--chat-template auto|qwen35|chatml] [--lora <adapter.gguf>]
//                  [--control-vector <raw.f32>]
#include "wwc_engine.h"
#include "wwc_http.h"
#include "wwc_server.h"

#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <string>
#include <thread>

namespace {

wwc::HttpServer * g_server = nullptr;

void on_signal(int) {
    if (g_server) {
        g_server->stop();
    }
}

void print_help() {
    std::printf(
        "world-work-cpp - local llama.cpp compatible inference server\n"
        "\n"
        "  -m, --model PATH        gguf model file (required)\n"
        "      --host HOST         listen address (default 127.0.0.1)\n"
        "  -p, --port PORT         listen port (default 8080)\n"
        "  -c, --ctx-size N        context size per slot (default 16384)\n"
        "      --n-gpu-layers N    layers offloaded to the GPU (default 999)\n"
        "      --parallel N        number of slots (default 1)\n"
        "  -t, --threads N         generation threads\n"
        "      --threads-batch N   prompt processing threads\n"
        "      --batch-size N      logical batch size (default 512)\n"
        "      --ubatch-size N     physical batch size (default 512)\n"
        "      --n-rs-seq N        recurrent state snapshots per slot (default 4)\n"
        "      --ctx-checkpoints N context checkpoints kept per slot (default 2, 0 disables)\n"
        "      --checkpoint-min-step N  minimum spacing between checkpoints (default 0)\n"
        "      --cache-type-k T    K cache type (default q8_0)\n"
        "      --cache-type-v T    V cache type (default q8_0)\n"
        "  -fa, --flash-attn       enable flash attention\n"
        "      --no-mmap           do not memory map the model\n"
        "      --lora PATH         attach a lora adapter (gguf)\n"
        "      --lora-scale F      lora scale (default 1.0)\n"
        "      --control-vector F  raw float32 control vector (n_embd * (n_layers - 1) float32)\n"
        "      --cvec-scale F      control vector strength multiplier (default 1.0)\n"
        "      --cvec-layer-start N first layer to inject (default 1)\n"
        "      --cvec-layer-end N   last layer to inject, inclusive (default n_layers - 1)\n"
        "      --chat-template M   auto | qwen35 | chatml (default auto)\n"
        "      --thinking          keep the <think> block open in the prompt\n"
        "      --seed N            default sampling seed\n"
        "      --temp F            default temperature\n"
        "      --top-p F           default top_p\n"
        "      --top-k N           default top_k\n"
        "      --n-predict N       default max tokens\n"
        "      --verbose           verbose model loading\n"
        "  -h, --help              show this help\n");
}

bool need_value(int argc, char ** argv, int & i, const char * name, std::string & out) {
    if (i + 1 >= argc) {
        std::fprintf(stderr, "missing value for %s\n", name);
        return false;
    }
    out = argv[++i];
    return true;
}

} // namespace

int main(int argc, char ** argv) {
    wwc::Engine::Config cfg;
    wwc::ServerOptions opts;
    std::string host = "127.0.0.1";
    int port = 8080;
    bool have_seed = false;
    uint32_t seed = LLAMA_DEFAULT_SEED;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        std::string v;
        if (a == "-h" || a == "--help") {
            print_help();
            return 0;
        } else if (a == "-m" || a == "--model") {
            if (!need_value(argc, argv, i, "--model", cfg.model_path)) return 2;
        } else if (a == "--host") {
            if (!need_value(argc, argv, i, "--host", host)) return 2;
        } else if (a == "-p" || a == "--port") {
            if (!need_value(argc, argv, i, "--port", v)) return 2;
            port = std::atoi(v.c_str());
        } else if (a == "-c" || a == "--ctx-size") {
            if (!need_value(argc, argv, i, "--ctx-size", v)) return 2;
            cfg.n_ctx = std::atoi(v.c_str());
        } else if (a == "--n-gpu-layers") {
            if (!need_value(argc, argv, i, "--n-gpu-layers", v)) return 2;
            cfg.n_gpu_layers = std::atoi(v.c_str());
        } else if (a == "--parallel" || a == "-np") {
            if (!need_value(argc, argv, i, "--parallel", v)) return 2;
            cfg.n_parallel = std::atoi(v.c_str());
        } else if (a == "-t" || a == "--threads") {
            if (!need_value(argc, argv, i, "--threads", v)) return 2;
            cfg.n_threads = std::atoi(v.c_str());
        } else if (a == "--threads-batch" || a == "-tb") {
            if (!need_value(argc, argv, i, "--threads-batch", v)) return 2;
            cfg.n_threads_batch = std::atoi(v.c_str());
        } else if (a == "--batch-size") {
            if (!need_value(argc, argv, i, "--batch-size", v)) return 2;
            cfg.n_batch = std::atoi(v.c_str());
        } else if (a == "--ubatch-size") {
            if (!need_value(argc, argv, i, "--ubatch-size", v)) return 2;
            cfg.n_ubatch = std::atoi(v.c_str());
        } else if (a == "--ctx-checkpoints") {
            if (!need_value(argc, argv, i, "--ctx-checkpoints", v)) return 2;
            cfg.n_ctx_checkpoints = std::atoi(v.c_str());
        } else if (a == "--checkpoint-min-step") {
            if (!need_value(argc, argv, i, "--checkpoint-min-step", v)) return 2;
            cfg.checkpoint_min_step = std::atoi(v.c_str());
        } else if (a == "--n-rs-seq") {
            if (!need_value(argc, argv, i, "--n-rs-seq", v)) return 2;
            cfg.n_rs_seq = std::atoi(v.c_str());
        } else if (a == "--cache-type-k") {
            if (!need_value(argc, argv, i, "--cache-type-k", cfg.cache_type_k)) return 2;
        } else if (a == "--cache-type-v") {
            if (!need_value(argc, argv, i, "--cache-type-v", cfg.cache_type_v)) return 2;
        } else if (a == "-fa" || a == "--flash-attn") {
            cfg.flash_attn = true;
        } else if (a == "--no-mmap") {
            cfg.use_mmap = false;
        } else if (a == "--lora") {
            if (!need_value(argc, argv, i, "--lora", cfg.lora_path)) return 2;
        } else if (a == "--lora-scale") {
            if (!need_value(argc, argv, i, "--lora-scale", v)) return 2;
            cfg.lora_scale = static_cast<float>(std::atof(v.c_str()));
        } else if (a == "--control-vector") {
            if (!need_value(argc, argv, i, "--control-vector", cfg.cvec_path)) return 2;
        } else if (a == "--cvec-scale") {
            if (!need_value(argc, argv, i, "--cvec-scale", v)) return 2;
            cfg.cvec_scale = static_cast<float>(std::atof(v.c_str()));
        } else if (a == "--cvec-layer-start") {
            if (!need_value(argc, argv, i, "--cvec-layer-start", v)) return 2;
            cfg.cvec_layer_start = std::atoi(v.c_str());
        } else if (a == "--cvec-layer-end") {
            if (!need_value(argc, argv, i, "--cvec-layer-end", v)) return 2;
            cfg.cvec_layer_end = std::atoi(v.c_str());
        } else if (a == "--chat-template") {
            if (!need_value(argc, argv, i, "--chat-template", cfg.chat_template_mode)) return 2;
        } else if (a == "--thinking") {
            opts.enable_thinking = true;
        } else if (a == "--seed") {
            if (!need_value(argc, argv, i, "--seed", v)) return 2;
            seed = static_cast<uint32_t>(std::strtoul(v.c_str(), nullptr, 10));
            have_seed = true;
        } else if (a == "--temp" || a == "--temperature") {
            if (!need_value(argc, argv, i, "--temp", v)) return 2;
            opts.defaults.temperature = static_cast<float>(std::atof(v.c_str()));
        } else if (a == "--top-p") {
            if (!need_value(argc, argv, i, "--top-p", v)) return 2;
            opts.defaults.top_p = static_cast<float>(std::atof(v.c_str()));
        } else if (a == "--top-k") {
            if (!need_value(argc, argv, i, "--top-k", v)) return 2;
            opts.defaults.top_k = std::atoi(v.c_str());
        } else if (a == "--n-predict") {
            if (!need_value(argc, argv, i, "--n-predict", v)) return 2;
            opts.defaults.max_tokens = std::atoi(v.c_str());
        } else if (a == "--verbose") {
            cfg.verbose = true;
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", a.c_str());
            print_help();
            return 2;
        }
    }

    if (cfg.model_path.empty()) {
        std::fprintf(stderr, "--model is required\n");
        print_help();
        return 2;
    }

    if (have_seed) {
        opts.defaults.seed = seed;
        opts.defaults.has_seed = true;
    }

    std::printf("world-work-cpp loading %s\n", cfg.model_path.c_str());
    std::printf("  ctx=%d parallel=%d gpu_layers=%d cache=%s/%s\n",
                cfg.n_ctx, cfg.n_parallel, cfg.n_gpu_layers,
                cfg.cache_type_k.c_str(), cfg.cache_type_v.c_str());
    std::fflush(stdout);

    wwc::Engine engine(cfg);
    std::string err;
    if (!engine.load(&err)) {
        std::fprintf(stderr, "load failed: %s\n", err.c_str());
        return 1;
    }
    std::printf("  model: %s (n_vocab=%d n_ctx_train=%d template=%s)\n",
                engine.model_desc().c_str(), engine.n_vocab(),
                engine.n_ctx_train(), engine.template_mode().c_str());
    std::fflush(stdout);

    wwc::HttpServer server(host, port);
    g_server = &server;
    std::signal(SIGINT, on_signal);
    std::signal(SIGTERM, on_signal);

    wwc::ApiServer api(engine, opts);
    api.register_routes(server);

    std::printf("  listening on http://%s:%d\n", host.c_str(), port);
    std::fflush(stdout);

    try {
        server.run();
    } catch (const std::exception & ex) {
        std::fprintf(stderr, "server error: %s\n", ex.what());
        return 1;
    }
    g_server = nullptr;
    return 0;
}
