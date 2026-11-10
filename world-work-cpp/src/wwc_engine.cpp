#include "wwc_engine.h"

#include "ggml-backend.h"
#include "ggml.h"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>

namespace wwc {

namespace {

double now_ms() {
    using clock = std::chrono::steady_clock;
    return std::chrono::duration<double, std::milli>(clock::now().time_since_epoch()).count();
}

ggml_type cache_type_from_name(const std::string & name) {
    if (name.empty() || name == "default") return GGML_TYPE_COUNT;
    if (name == "f32")  return GGML_TYPE_F32;
    if (name == "f16")  return GGML_TYPE_F16;
    if (name == "bf16") return GGML_TYPE_BF16;
    if (name == "q8_0") return GGML_TYPE_Q8_0;
    if (name == "q4_0") return GGML_TYPE_Q4_0;
    if (name == "q4_k") return GGML_TYPE_Q4_K;
    return GGML_TYPE_COUNT;
}

// Residual capture: the mean of the residual stream per layer.
//
// The upstream control vector is added straight to this stream
// (residual[il] += v[il]), so the mean over the answer tokens is exactly
// what the voice difference has to be measured against. The graph node
// named "l_out-<il>" is that stream at the end of a layer.
struct ResidualCapture {
    int      n_embd       = 0;
    int      from_token   = 0;
    int      total_tokens = 0;
    // The batch is decoded in chunks: the callback sees one chunk at a time,
    // so it needs to know where the chunk starts to place the rows.
    int      chunk_start  = 0;
    int      chunk_tokens = 0;
    bool     enabled      = false;  // capture only during the capture decode
    std::map<int, std::vector<double>> sums;
    std::map<int, int64_t> counts;
};

bool capture_eval_cb(struct ggml_tensor * t, bool ask, void * user_data) {
    ResidualCapture * cap = static_cast<ResidualCapture *>(user_data);
    if (!cap) {
        return true;
    }
    if (ask) {
        if (!cap->enabled) {
            return false;
        }
        const char * name = ggml_get_name(t);
        return name && std::strncmp(name, "l_out", 5) == 0;
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
        int64_t global;
        if (n_tok == cap->chunk_tokens) {
            // the whole chunk was computed for this layer
            global = cap->chunk_start + row;
        } else if (n_tok == 1) {
            // the last layer is only computed for the chunk's last token
            global = cap->chunk_start + cap->chunk_tokens - 1;
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

// Length of the longest suffix of s that is a proper prefix of marker.
size_t partial_suffix_len(const std::string & s, const char * marker) {
    const size_t m = std::strlen(marker);
    if (m < 2) return 0;
    const size_t max_k = std::min(m - 1, s.size());
    for (size_t k = max_k; k > 0; --k) {
        if (s.compare(s.size() - k, k, marker, k) == 0) return k;
    }
    return 0;
}

// Length of the longest prefix of s that ends on a UTF-8 character boundary.
size_t utf8_complete_prefix(const std::string & s) {
    const size_t n = s.size();
    if (n == 0) return 0;
    size_t i = n;
    size_t back = 0;
    while (i > 0 && back < 4) {
        const unsigned char c = static_cast<unsigned char>(s[i - 1]);
        if ((c & 0x80) == 0x00) return i;                 // ascii
        if ((c & 0xC0) == 0x80) { --i; ++back; continue; }  // continuation byte
        size_t need = 1;
        if ((c & 0xE0) == 0xC0) need = 2;
        else if ((c & 0xF0) == 0xE0) need = 3;
        else if ((c & 0xF8) == 0xF0) need = 4;
        const size_t avail = n - (i - 1);
        return avail >= need ? n : (i - 1);
    }
    return n;
}

// Splits the generated stream into reasoning and content, honours stop
// strings and never emits a half UTF-8 character.
class StreamSplitter {
public:
    StreamSplitter(const std::vector<std::string> & stops, const TokenCallback & sink)
        : stops_(stops), sink_(sink) {}

    void feed(const std::string & piece) {
        pending_ += piece;
        process();
    }

    void flush() {
        if (!pending_.empty() && !aborted_) {
            const std::string rest = pending_;
            pending_.clear();
            emit(rest, in_reasoning_);
        }
    }

    const std::string & content() const { return content_; }
    const std::string & reasoning() const { return reasoning_; }
    bool stop_hit() const { return stop_hit_; }
    // Set when the sink reported that the client is gone.
    bool aborted() const { return aborted_; }

private:
    void emit(const std::string & text, bool is_reasoning) {
        if (text.empty() || aborted_) return;
        if (is_reasoning) {
            reasoning_ += text;
        } else {
            content_ += text;
            for (const std::string & s : stops_) {
                if (s.empty()) continue;
                size_t p = content_.find(s);
                if (p != std::string::npos) {
                    content_ = content_.substr(0, p);
                    stop_hit_ = true;
                    break;
                }
            }
        }
        if (sink_ && !sink_(text, is_reasoning)) {
            aborted_ = true;
        }
    }

    void process() {
        while (!pending_.empty() && !stop_hit_ && !aborted_) {
            const char * marker = in_reasoning_ ? "</think>" : "<think>";
            const size_t mlen = std::strlen(marker);
            size_t p = pending_.find(marker);
            if (p == std::string::npos) {
                const size_t keep = partial_suffix_len(pending_, marker);
                size_t cut = pending_.size() - keep;
                size_t safe = utf8_complete_prefix(pending_.substr(0, cut));
                emit(pending_.substr(0, safe), in_reasoning_);
                pending_.erase(0, safe);
                break;
            }
            emit(pending_.substr(0, p), in_reasoning_);
            pending_.erase(0, p);
            pending_.erase(0, mlen);
            in_reasoning_ = !in_reasoning_;
        }
    }

    std::vector<std::string> stops_;
    TokenCallback sink_;
    bool aborted_ = false;
    std::string pending_;
    std::string content_;
    std::string reasoning_;
    bool in_reasoning_ = false;
    bool stop_hit_ = false;
};

llama_sampler * build_sampler(const llama_vocab * vocab, int32_t n_vocab, const SamplingParams & sp) {
    llama_sampler_chain_params cparams = llama_sampler_chain_default_params();
    cparams.no_perf = true;
    llama_sampler * chain = llama_sampler_chain_init(cparams);

    // The grammar sampler goes first, like the upstream server does: it
    // filters the candidates before anything else touches them.
    if (!sp.grammar.empty()) {
        llama_sampler * grammar = llama_sampler_init_grammar(vocab, sp.grammar.c_str(), "root");
        if (grammar) {
            llama_sampler_chain_add(chain, grammar);
        } else {
            std::fprintf(stderr, "[sampler] grammar rejected, continuing without it\n");
        }
    }
    if (sp.repeat_penalty != 1.0f || sp.presence_penalty != 0.0f || sp.frequency_penalty != 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_penalties(
            n_vocab, sp.repeat_last_n, sp.repeat_penalty, sp.frequency_penalty, sp.presence_penalty));
    }
    if (sp.dry_multiplier > 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_dry(
            vocab, sp.dry_multiplier, sp.dry_base, sp.dry_allowed_length, sp.dry_penalty_last_n, nullptr, 0));
    }
    if (sp.top_k > 0) {
        llama_sampler_chain_add(chain, llama_sampler_init_top_k(sp.top_k));
    }
    if (sp.top_n_sigma > 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_top_n_sigma(sp.top_n_sigma));
    }
    if (sp.typical_p > 0.0f && sp.typical_p < 1.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_typical(sp.typical_p, sp.min_keep));
    }
    if (sp.top_p > 0.0f && sp.top_p < 1.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_top_p(sp.top_p, sp.min_keep));
    }
    if (sp.min_p > 0.0f && sp.min_p < 1.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_min_p(sp.min_p, sp.min_keep));
    }
    if (sp.xtc_probability > 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_xtc(sp.xtc_probability, sp.xtc_threshold, sp.min_keep, sp.seed));
    }
    if (sp.dynatemp_range > 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_temp_ext(sp.temperature, sp.dynatemp_range, sp.dynatemp_exponent));
    } else {
        llama_sampler_chain_add(chain, llama_sampler_init_temp(sp.temperature));
    }
    if (sp.mirostat == 1) {
        llama_sampler_chain_add(chain, llama_sampler_init_mirostat(
            n_vocab, sp.seed, sp.mirostat_tau, sp.mirostat_eta, 100));
    } else if (sp.mirostat == 2) {
        llama_sampler_chain_add(chain, llama_sampler_init_mirostat_v2(
            sp.seed, sp.mirostat_tau, sp.mirostat_eta));
    } else if (sp.temperature <= 0.0f) {
        llama_sampler_chain_add(chain, llama_sampler_init_greedy());
    } else {
        llama_sampler_chain_add(chain, llama_sampler_init_dist(sp.seed));
    }
    return chain;
}

} // namespace

Engine::Engine(const Config & cfg) : cfg_(cfg) {}

Engine::~Engine() {
    unload();
}

void Engine::unload() {
    if (ctx_) {
        llama_free(ctx_);
        ctx_ = nullptr;
    }
    if (model_) {
        llama_model_free(model_);
        model_ = nullptr;
    }
    vocab_ = nullptr;
}

// Swaps the model file in place. The upstream server needs a restart
// for this; here the process stays up and the connections survive.
bool Engine::reload_model(const std::string & path, std::string * err) {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    if (path.empty()) {
        *err = "model path required";
        return false;
    }
    std::ifstream probe(path, std::ios::binary);
    if (!probe) {
        *err = "cannot open model file: " + path;
        return false;
    }
    unload();
    cfg_.model_path = path;
    // the chat template and the vector belonged to the previous model
    chat_template_.clear();
    template_mode_ = "auto";
    cvec_cur_path_.clear();
    cvec_default_path_.clear();
    cvec_cur_scale_ = 1.0f;
    cvec_default_scale_ = 1.0f;
    cvec_cur_layer_start_ = 1;
    cvec_default_layer_start_ = 1;
    cvec_cur_layer_end_ = -1;
    cvec_default_layer_end_ = -1;
    if (!load_model(err)) {
        return false;
    }
    if (!load_context(err)) {
        return false;
    }
    std::fprintf(stderr, "[model] reloaded: %s\n", path.c_str());
    return true;
}

bool Engine::load(std::string * err) {
    if (!load_model(err)) return false;
    if (!load_context(err)) return false;
    if (!cfg_.lora_path.empty() && !apply_lora(err)) return false;
    if (!cfg_.cvec_path.empty() && !apply_control_vector(err)) return false;
    return true;
}

bool Engine::load_model(std::string * err) {
    llama_backend_init();

    llama_model_params mparams = llama_model_default_params();
    mparams.n_gpu_layers = cfg_.n_gpu_layers;
    mparams.load_mode = cfg_.use_mmap ? LLAMA_LOAD_MODE_MMAP : LLAMA_LOAD_MODE_NONE;
    if (!cfg_.verbose) {
        mparams.progress_callback = nullptr;
    }

    model_ = llama_model_load_from_file(cfg_.model_path.c_str(), mparams);
    if (!model_) {
        *err = "failed to load model: " + cfg_.model_path;
        return false;
    }

    vocab_ = llama_model_get_vocab(model_);
    n_vocab_ = llama_vocab_n_tokens(vocab_);
    n_ctx_train_ = llama_model_n_ctx_train(model_);
    n_embd_ = llama_model_n_embd(model_);
    n_layers_ = llama_model_n_layer(model_);

    char desc[512];
    desc[0] = '\0';
    llama_model_desc(model_, desc, sizeof(desc));
    model_desc_ = desc;

    // read the raw chat template out of the GGUF metadata
    {
        std::vector<char> buf(1 << 16, 0);
        int32_t n = llama_model_meta_val_str(model_, "tokenizer.chat_template", buf.data(), buf.size());
        if (n > 0) {
            chat_template_.assign(buf.data(), static_cast<size_t>(n));
        }
    }

    if (cfg_.chat_template_mode == "auto") {
        template_mode_ = looks_like_qwen35(chat_template_) ? "qwen35" : "chatml";
    } else {
        template_mode_ = cfg_.chat_template_mode;
    }
    return true;
}

bool Engine::load_context(std::string * err) {
    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = static_cast<uint32_t>(cfg_.n_ctx * std::max(1, cfg_.n_parallel));
    cparams.n_seq_max = static_cast<uint32_t>(std::max(1, cfg_.n_parallel));
    cparams.n_batch = static_cast<uint32_t>(cfg_.n_batch);
    cparams.n_ubatch = static_cast<uint32_t>(cfg_.n_ubatch);
    if (cfg_.n_threads > 0) {
        cparams.n_threads = cfg_.n_threads;
        cparams.n_threads_batch = cfg_.n_threads_batch > 0 ? cfg_.n_threads_batch : cfg_.n_threads;
    }
    cparams.type_k = cache_type_from_name(cfg_.cache_type_k);
    cparams.type_v = cache_type_from_name(cfg_.cache_type_v);
    cparams.flash_attn_type = cfg_.flash_attn ? LLAMA_FLASH_ATTN_TYPE_ENABLED : LLAMA_FLASH_ATTN_TYPE_AUTO;
    cparams.n_rs_seq = static_cast<uint32_t>(std::max(0, cfg_.n_rs_seq));
    cparams.no_perf = true;

    ctx_ = llama_init_from_model(model_, cparams);
    if (!ctx_) {
        *err = "failed to create context";
        return false;
    }

    slots_.clear();
    for (int i = 0; i < std::max(1, cfg_.n_parallel); ++i) {
        Slot s;
        s.seq_id = static_cast<llama_seq_id>(i);
        slots_.push_back(std::move(s));
    }
    return true;
}

// Loads or unloads a LoRA adapter at runtime.
bool Engine::set_lora(const std::string & path, float scale, std::string * err) {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    if (path.empty()) {
        const int32_t rc = llama_set_adapters_lora(ctx_, nullptr, 0, nullptr);
        if (rc != 0) {
            *err = "failed to unload lora adapter";
            return false;
        }
        if (lora_) {
            llama_adapter_lora_free(lora_);
            lora_ = nullptr;
        }
        lora_loaded_path_.clear();
        // the effective model changed: cached keys and values are stale
        clear_kv_cache();
        std::fprintf(stderr, "[lora] unloaded\n");
        return true;
    }
    llama_adapter_lora * adapter = llama_adapter_lora_init(model_, path.c_str());
    if (!adapter) {
        *err = "failed to load lora adapter: " + path;
        return false;
    }
    llama_adapter_lora * adapters[1] = { adapter };
    float use_scale = scale;
    const int32_t rc = llama_set_adapters_lora(ctx_, adapters, 1, &use_scale);
    if (rc != 0) {
        llama_adapter_lora_free(adapter);
        *err = "failed to attach lora adapter";
        return false;
    }
    if (lora_) {
        llama_adapter_lora_free(lora_);
    }
    lora_ = adapter;
    lora_loaded_path_ = path;
    clear_kv_cache();
    std::fprintf(stderr, "[lora] applied: %s (scale %.3f)\n", path.c_str(), scale);
    return true;
}

std::string Engine::lora_path() const {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    return lora_loaded_path_;
}

bool Engine::apply_lora(std::string * err) {
    llama_adapter_lora * adapter = llama_adapter_lora_init(model_, cfg_.lora_path.c_str());
    if (!adapter) {
        *err = "failed to load lora adapter: " + cfg_.lora_path;
        return false;
    }
    llama_adapter_lora * adapters[1] = { adapter };
    float scale = cfg_.lora_scale;
    int32_t rc = llama_set_adapters_lora(ctx_, adapters, 1, &scale);
    if (rc != 0) {
        *err = "failed to attach lora adapter";
        return false;
    }
    lora_ = adapter;
    lora_loaded_path_ = cfg_.lora_path;
    return true;
}

bool Engine::apply_control_vector(std::string * err) {
    return set_control_vector(cfg_.cvec_path, cfg_.cvec_scale,
                              cfg_.cvec_layer_start, cfg_.cvec_layer_end, err);
}

// Loads a control vector from a raw float32 file and applies it to the live
// context. The file layout is the upstream one: n_embd floats per model
// layer, layer 0 omitted, so the file holds n_layers - 1 segments.
// The scale is applied before the upload, which keeps the file on disk
// reusable at different strengths.
bool Engine::set_control_vector(const std::string & path, float scale,
                                 int32_t layer_start, int32_t layer_end,
                                 std::string * err) {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    // This is the server default: requests that carry no vector of their own
    // fall back to it.
    cvec_default_path_        = path;
    cvec_default_scale_       = scale;
    cvec_default_layer_start_ = layer_start;
    cvec_default_layer_end_   = layer_end;
    return set_control_vector_locked(path, scale, layer_start, layer_end, err);
}

Engine::ControlVectorState Engine::control_vector_state() {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    ControlVectorState st;
    st.path        = cvec_cur_path_;
    st.scale       = cvec_cur_scale_;
    st.layer_start = cvec_cur_layer_start_;
    st.layer_end   = cvec_cur_layer_end_;
    st.active      = !cvec_cur_path_.empty();
    st.is_default  = st.active && !cvec_default_path_.empty()
                     && cvec_default_path_ == cvec_cur_path_
                     && cvec_default_scale_ == cvec_cur_scale_;
    return st;
}

bool Engine::generate_voice_vector(const VoiceSpec & spec, VoiceVector & out, std::string * err) {
    if (!model_ || !vocab_) {
        if (err) *err = "no model loaded";
        return false;
    }
    if (spec.positive.empty()) {
        if (err) *err = "no voice pairs";
        return false;
    }
    const int32_t n_seg = n_layers_ - 1;
    if (n_seg <= 0 || n_embd_ <= 0) {
        if (err) *err = "model has no layers";
        return false;
    }

    // The GPU is the bottleneck: other requests wait while the capture runs.
    std::lock_guard<std::mutex> lock(ctx_mutex_);

    ResidualCapture cap;
    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = static_cast<uint32_t>(std::max(512, spec.capture_ctx));
    cparams.n_seq_max = 1;
    cparams.n_batch = static_cast<uint32_t>(cfg_.n_batch);
    cparams.n_ubatch = static_cast<uint32_t>(cfg_.n_ubatch);
    if (cfg_.n_threads > 0) {
        cparams.n_threads = cfg_.n_threads;
        cparams.n_threads_batch = cfg_.n_threads_batch > 0 ? cfg_.n_threads_batch : cfg_.n_threads;
    }
    cparams.type_k = cache_type_from_name(cfg_.cache_type_k);
    cparams.type_v = cache_type_from_name(cfg_.cache_type_v);
    cparams.no_perf = true;
    cparams.cb_eval = capture_eval_cb;
    cparams.cb_eval_user_data = &cap;

    struct CtxGuard {
        llama_context * ctx = nullptr;
        ~CtxGuard() { if (ctx) llama_free(ctx); }
    } guard;
    guard.ctx = llama_init_from_model(model_, cparams);
    if (!guard.ctx) {
        if (err) *err = "failed to create the capture context";
        return false;
    }
    llama_context * cap_ctx = guard.ctx;

    const std::string neutral = spec.neutral_persona.empty()
        ? std::string("You are a helpful assistant. Answer plainly and briefly.")
        : spec.neutral_persona;

    // Greedy answer: the voice sample has to be reproducible, and a sampled
    // answer would only add noise to the difference.
    auto generate_answer = [&](const std::string & persona, const std::string & question,
                               int32_t max_tokens, std::string & answer, int32_t variant = 0) -> bool {
        std::vector<ChatMsg> msgs;
        msgs.push_back(ChatMsg{ "system", persona, "" });
        msgs.push_back(ChatMsg{ "user", question, "" });
        const std::string prompt_text = render_prompt(msgs, true, false);
        std::vector<llama_token> toks;
        if (!tokenize(prompt_text, toks, true, true, err)) {
            return false;
        }
        if (toks.empty()) {
            if (err) *err = "empty prompt";
            return false;
        }
        llama_memory_clear(llama_get_memory(cap_ctx), true);
        // llama.cpp aborts the process when a batch is larger than n_batch,
        // so every decode here goes in chunks.
        const size_t chunk = static_cast<size_t>(std::max(1, cfg_.n_batch));
        bool decoded = true;
        for (size_t off = 0; off < toks.size(); off += chunk) {
            const size_t n = std::min(chunk, toks.size() - off);
            llama_batch batch = llama_batch_init((int) n, 0, 1);
            batch.n_tokens = (int) n;
            for (size_t t = 0; t < n; ++t) {
                batch.token[t] = toks[off + t];
                batch.pos[t] = (llama_pos) (off + t);
                batch.n_seq_id[t] = 1;
                batch.seq_id[t][0] = 0;
                batch.logits[t] = 0;
            }
            batch.logits[n - 1] = 1;
            const int rc = llama_decode(cap_ctx, batch);
            llama_batch_free(batch);
            if (rc != 0) {
                decoded = false;
                break;
            }
        }
        if (!decoded) {
            if (err) *err = "prompt decode failed";
            return false;
        }
        SamplingParams sp;
        sp.temperature = 0.0f;
        sp.top_p = 1.0f;
        sp.top_k = 1;
        sp.seed = 1234 + static_cast<uint32_t>(variant) * 7919u;
        sp.has_seed = true;
        llama_sampler * smpl = build_sampler(vocab_, n_vocab_, sp);
        std::vector<llama_token> generated;
        llama_batch one = llama_batch_init(1, 0, 1);
        llama_pos pos = (llama_pos) toks.size();
        for (int32_t i = 0; i < max_tokens; ++i) {
            const llama_token id = llama_sampler_sample(smpl, cap_ctx, -1);
            if (llama_vocab_is_eog(vocab_, id)) {
                break;
            }
            generated.push_back(id);
            one.n_tokens = 1;
            one.token[0] = id;
            one.pos[0] = pos;
            one.n_seq_id[0] = 1;
            one.seq_id[0][0] = 0;
            one.logits[0] = 1;
            if (llama_decode(cap_ctx, one) != 0) {
                break;
            }
            ++pos;
        }
        llama_batch_free(one);
        llama_sampler_free(smpl);
        if (generated.empty()) {
            if (err) *err = "no answer generated";
            return false;
        }
        return detokenize(generated, answer);
    };

    // Capture the residual mean over the answer tokens of one pair.
    auto capture = [&](const std::string & persona, const std::string & question,
                       const std::string & answer, std::vector<double> & flat) -> bool {
        std::vector<ChatMsg> msgs;
        msgs.push_back(ChatMsg{ "system", persona, "" });
        msgs.push_back(ChatMsg{ "user", question, "" });
        const std::string prompt_text = render_prompt(msgs, true, false);
        std::vector<llama_token> ptoks;
        std::vector<llama_token> atoks;
        if (!tokenize(prompt_text, ptoks, true, true, err)) {
            return false;
        }
        if (!tokenize(answer, atoks, false, false, err)) {
            return false;
        }
        if (atoks.empty()) {
            if (err) *err = "empty answer";
            return false;
        }
        std::vector<llama_token> all = ptoks;
        all.insert(all.end(), atoks.begin(), atoks.end());
        if ((int) all.size() > spec.capture_ctx) {
            if (err) *err = "prompt and answer do not fit the capture context";
            return false;
        }
        cap.sums.clear();
        cap.counts.clear();
        cap.from_token = (int) ptoks.size();
        cap.total_tokens = (int) all.size();
        cap.enabled = true;
        llama_memory_clear(llama_get_memory(cap_ctx), true);
        const size_t chunk = static_cast<size_t>(std::max(1, cfg_.n_batch));
        bool decoded = true;
        for (size_t off = 0; off < all.size(); off += chunk) {
            const size_t n = std::min(chunk, all.size() - off);
            llama_batch batch = llama_batch_init((int) n, 0, 1);
            batch.n_tokens = (int) n;
            for (size_t t = 0; t < n; ++t) {
                batch.token[t] = all[off + t];
                batch.pos[t] = (llama_pos) (off + t);
                batch.n_seq_id[t] = 1;
                batch.seq_id[t][0] = 0;
                batch.logits[t] = 0;
            }
            batch.logits[n - 1] = 1;
            cap.chunk_start = (int) off;
            cap.chunk_tokens = (int) n;
            const int rc = llama_decode(cap_ctx, batch);
            llama_batch_free(batch);
            if (rc != 0) {
                decoded = false;
                break;
            }
        }
        cap.enabled = false;
        if (!decoded) {
            if (err) *err = "capture decode failed";
            return false;
        }
        flat.assign((size_t) n_seg * (size_t) n_embd_, 0.0);
        for (int il = 1; il < n_layers_; ++il) {
            auto it = cap.sums.find(il);
            if (it == cap.sums.end() || cap.counts[il] == 0) {
                continue;
            }
            const double cnt = (double) cap.counts[il];
            const size_t base = (size_t) (il - 1) * (size_t) n_embd_;
            for (int d = 0; d < n_embd_; ++d) {
                flat[base + (size_t) d] = it->second[(size_t) d] / cnt;
            }
        }
        return true;
    };

    // Two ways to build the plain side of the difference.
    //
    // Explicit negative pairs: the caller decides what the plain side says.
    //
    // Default: the plain side answers the same prompts on its own. Measured
    // on the four live cards, this is the difference that measures the
    // voice: the plain side writes a different answer, so what the
    // difference carries is how she talks rather than what she is talking
    // about.
    //
    // The paired alternative (the same words on both sides, only the persona
    // differs) is available through spec.paired. It is kept because it is
    // the natural control, but it measures the persona instead of the voice:
    // a vector built from it made the author's own lines less likely on all
    // four cards.
    const bool paired = spec.paired && spec.negative.empty();

    std::vector<std::vector<double>> pos_means;
    std::vector<std::vector<double>> neg_means;
    // A prompt without an answer gets several samples: one difference is
    // dominated by the topic of that single exchange, several are the voice.
    const int32_t repeat = std::max(1, spec.repeat);
    for (const VoicePair & p : spec.positive) {
        const int32_t rounds = p.answer.empty() ? repeat : 1;
        for (int32_t r = 0; r < rounds; ++r) {
            std::string answer = p.answer;
            if (answer.empty() && !generate_answer(spec.persona, p.prompt, spec.max_tokens, answer, r)) {
                return false;
            }
            std::vector<double> flat;
            if (!capture(spec.persona, p.prompt, answer, flat)) {
                return false;
            }
            pos_means.push_back(flat);
            if (paired) {
                // Same words, plain assistant persona.
                std::vector<double> plain;
                if (!capture(neutral, p.prompt, answer, plain)) {
                    return false;
                }
                neg_means.push_back(plain);
            }
        }
    }
    if (!paired) {
        // No explicit plain side: the plain side answers the same prompts on
        // its own. The prompts come from the character side, so both sides
        // talk about the same thing and the difference is left with how she
        // talks.
        std::vector<VoicePair> plain_spec = spec.negative;
        if (plain_spec.empty()) {
            plain_spec.reserve(spec.positive.size());
            for (const VoicePair & p : spec.positive) {
                plain_spec.push_back(VoicePair{ p.prompt, "" });
            }
        }
        for (const VoicePair & p : plain_spec) {
            const int32_t rounds = p.answer.empty() ? repeat : 1;
            for (int32_t r = 0; r < rounds; ++r) {
                std::string answer = p.answer;
                if (answer.empty() && !generate_answer(neutral, p.prompt, spec.max_tokens, answer, r)) {
                    return false;
                }
                std::vector<double> flat;
                if (!capture(neutral, p.prompt, answer, flat)) {
                    return false;
                }
                neg_means.push_back(flat);
            }
        }
    }
    if (pos_means.empty() || neg_means.empty()) {
        if (err) *err = "no captures";
        return false;
    }

    out.data.clear();
    out.residual_norms.clear();
    out.factors.clear();
    out.ratio = spec.ratio;
    out.n_pairs = (int32_t) pos_means.size();
    std::vector<double> pm((size_t) n_embd_);
    std::vector<double> nm((size_t) n_embd_);
    std::vector<double> v((size_t) n_embd_);
    for (int k = 0; k < n_seg; ++k) {
        const size_t base = (size_t) k * (size_t) n_embd_;
        std::fill(pm.begin(), pm.end(), 0.0);
        std::fill(nm.begin(), nm.end(), 0.0);
        for (const std::vector<double> & m : pos_means) {
            for (int d = 0; d < n_embd_; ++d) {
                pm[(size_t) d] += m[base + (size_t) d];
            }
        }
        for (const std::vector<double> & m : neg_means) {
            for (int d = 0; d < n_embd_; ++d) {
                nm[(size_t) d] += m[base + (size_t) d];
            }
        }
        for (int d = 0; d < n_embd_; ++d) {
            pm[(size_t) d] /= (double) pos_means.size();
            nm[(size_t) d] /= (double) neg_means.size();
            v[(size_t) d] = pm[(size_t) d] - nm[(size_t) d];
        }
        double vn = 0.0;
        for (int d = 0; d < n_embd_; ++d) {
            vn += v[(size_t) d] * v[(size_t) d];
        }
        vn = std::sqrt(vn);
        double rn = 0.0;
        for (const std::vector<double> & m : pos_means) {
            double s = 0.0;
            for (int d = 0; d < n_embd_; ++d) {
                s += m[base + (size_t) d] * m[base + (size_t) d];
            }
            rn += std::sqrt(s);
        }
        rn /= (double) pos_means.size();
        const double target = spec.ratio * rn;
        const double factor = (vn > 0.0) ? target / vn : 0.0;
        for (int d = 0; d < n_embd_; ++d) {
            out.data.push_back((float) (v[(size_t) d] * factor));
        }
        out.residual_norms.push_back(rn);
        out.factors.push_back(factor);
    }
    return true;
}

bool Engine::verify_voice_vector(const VoiceSpec & spec, const std::string & vector_path,
                                 float scale, VoiceVerifyResult & out, std::string * err) {
    if (!model_ || !vocab_) {
        if (err) *err = "no model loaded";
        return false;
    }
    if (spec.positive.empty()) {
        if (err) *err = "no voice samples";
        return false;
    }
    std::lock_guard<std::mutex> lock(ctx_mutex_);

    const int32_t n_layers = n_layers_;
    const int32_t n_embd = n_embd_;

    // A vector belongs to one model: the size has to match before anything
    // is measured, otherwise the numbers would be about the wrong thing.
    const size_t want = static_cast<size_t>(n_embd) * static_cast<size_t>(n_layers - 1);
    std::ifstream size_check(vector_path, std::ios::binary | std::ios::ate);
    const std::streamoff file_bytes = size_check ? size_check.tellg() : -1;
    if (file_bytes < 0 || static_cast<size_t>(file_bytes) != want * sizeof(float)) {
        if (err) *err = "control vector file size mismatch";
        return false;
    }
    std::vector<float> data(want, 0.0f);
    std::ifstream f(vector_path, std::ios::binary);
    f.read(reinterpret_cast<char *>(data.data()), static_cast<std::streamsize>(want * sizeof(float)));
    if (scale != 1.0f) {
        for (float & v : data) {
            v *= scale;
        }
    }

    llama_context_params cparams = llama_context_default_params();
    cparams.n_ctx = static_cast<uint32_t>(std::max(512, spec.capture_ctx));
    cparams.n_seq_max = 1;
    cparams.n_batch = static_cast<uint32_t>(cfg_.n_batch);
    cparams.n_ubatch = static_cast<uint32_t>(cfg_.n_ubatch);
    if (cfg_.n_threads > 0) {
        cparams.n_threads = cfg_.n_threads;
        cparams.n_threads_batch = cfg_.n_threads_batch > 0 ? cfg_.n_threads_batch : cfg_.n_threads;
    }
    cparams.type_k = cache_type_from_name(cfg_.cache_type_k);
    cparams.type_v = cache_type_from_name(cfg_.cache_type_v);
    cparams.no_perf = true;
    struct CtxGuard {
        llama_context * ctx = nullptr;
        ~CtxGuard() { if (ctx) llama_free(ctx); }
    } guard;
    guard.ctx = llama_init_from_model(model_, cparams);
    if (!guard.ctx) {
        if (err) *err = "failed to create the scoring context";
        return false;
    }
    llama_context * ctx = guard.ctx;

    const std::string neutral = spec.neutral_persona.empty()
        ? std::string("You are a helpful assistant. Answer plainly and briefly.")
        : spec.neutral_persona;

    // Greedy answer, used when a sample has no text of its own.
    auto make_answer = [&](const std::string & persona, const std::string & question,
                           std::string & answer, int32_t variant = 0) -> bool {
        std::vector<ChatMsg> msgs;
        msgs.push_back(ChatMsg{ "system", persona, "" });
        msgs.push_back(ChatMsg{ "user", question, "" });
        const std::string prompt_text = render_prompt(msgs, true, false);
        std::vector<llama_token> toks;
        if (!tokenize(prompt_text, toks, true, true, err)) {
            return false;
        }
        if (toks.empty()) {
            if (err) *err = "empty prompt";
            return false;
        }
        llama_memory_clear(llama_get_memory(ctx), true);
        // llama.cpp aborts the process when a batch is larger than n_batch,
        // so every decode here goes in chunks.
        const size_t chunk = static_cast<size_t>(std::max(1, cfg_.n_batch));
        bool decoded = true;
        for (size_t off = 0; off < toks.size(); off += chunk) {
            const size_t n = std::min(chunk, toks.size() - off);
            llama_batch batch = llama_batch_init((int) n, 0, 1);
            batch.n_tokens = (int) n;
            for (size_t t = 0; t < n; ++t) {
                batch.token[t] = toks[off + t];
                batch.pos[t] = (llama_pos) (off + t);
                batch.n_seq_id[t] = 1;
                batch.seq_id[t][0] = 0;
                batch.logits[t] = 0;
            }
            batch.logits[n - 1] = 1;
            const int rc = llama_decode(ctx, batch);
            llama_batch_free(batch);
            if (rc != 0) {
                decoded = false;
                break;
            }
        }
        if (!decoded) {
            if (err) *err = "prompt decode failed";
            return false;
        }
        SamplingParams sp;
        sp.temperature = 0.0f;
        sp.top_p = 1.0f;
        sp.top_k = 1;
        sp.seed = 1234 + static_cast<uint32_t>(variant) * 7919u;
        sp.has_seed = true;
        llama_sampler * smpl = build_sampler(vocab_, n_vocab_, sp);
        std::vector<llama_token> generated;
        llama_batch one = llama_batch_init(1, 0, 1);
        llama_pos pos = (llama_pos) toks.size();
        for (int32_t i = 0; i < spec.max_tokens; ++i) {
            const llama_token id = llama_sampler_sample(smpl, ctx, -1);
            if (llama_vocab_is_eog(vocab_, id)) {
                break;
            }
            generated.push_back(id);
            one.n_tokens = 1;
            one.token[0] = id;
            one.pos[0] = pos;
            one.n_seq_id[0] = 1;
            one.seq_id[0][0] = 0;
            one.logits[0] = 1;
            if (llama_decode(ctx, one) != 0) {
                break;
            }
            ++pos;
        }
        llama_batch_free(one);
        llama_sampler_free(smpl);
        if (generated.empty()) {
            if (err) *err = "no answer generated";
            return false;
        }
        return detokenize(generated, answer);
    };

    // Teacher forced negative log likelihood of one sample, per answer token.
    auto score_one = [&](const std::string & persona, const VoicePair & pair,
                         double & nll, int32_t & tokens) -> bool {
        std::vector<ChatMsg> msgs;
        msgs.push_back(ChatMsg{ "system", persona, "" });
        msgs.push_back(ChatMsg{ "user", pair.prompt, "" });
        const std::string prompt_text = render_prompt(msgs, true, false);
        std::vector<llama_token> ptoks;
        std::vector<llama_token> atoks;
        if (!tokenize(prompt_text, ptoks, true, true, err)) {
            return false;
        }
        if (!tokenize(pair.answer, atoks, false, false, err)) {
            return false;
        }
        if (atoks.empty() || ptoks.empty()) {
            if (err) *err = "empty sample";
            return false;
        }
        std::vector<llama_token> all = ptoks;
        all.insert(all.end(), atoks.begin(), atoks.end());
        if ((int) all.size() > spec.capture_ctx) {
            if (err) *err = "sample does not fit the scoring context";
            return false;
        }
        // The last prompt token predicts the first answer token.
        const size_t first = ptoks.size() - 1;
        llama_memory_clear(llama_get_memory(ctx), true);
        // The logits of a chunk are read right after that chunk is decoded:
        // the next decode overwrites them.
        const size_t chunk = static_cast<size_t>(std::max(1, cfg_.n_batch));
        double total = 0.0;
        int32_t counted = 0;
        bool decoded = true;
        for (size_t off = 0; off < all.size(); off += chunk) {
            const size_t n = std::min(chunk, all.size() - off);
            llama_batch batch = llama_batch_init((int) n, 0, 1);
            batch.n_tokens = (int) n;
            for (size_t t = 0; t < n; ++t) {
                const size_t pos = off + t;
                batch.token[t] = all[pos];
                batch.pos[t] = (llama_pos) pos;
                batch.n_seq_id[t] = 1;
                batch.seq_id[t][0] = 0;
                batch.logits[t] = 0;
                if (pos >= first && pos + 1 < all.size()) {
                    batch.logits[t] = 1;
                }
            }
            const int rc = llama_decode(ctx, batch);
            llama_batch_free(batch);
            if (rc != 0) {
                decoded = false;
                break;
            }
            // The logits of one decode are stored contiguously, in the
            // order the positions with logits enabled appeared in the batch.
            const float * rows_ptr = llama_get_logits(ctx);
            if (!rows_ptr) {
                decoded = false;
                break;
            }
            int32_t row = 0;
            for (size_t t = 0; t < n; ++t) {
                const size_t pos = off + t;
                if (!(pos >= first && pos + 1 < all.size())) {
                    continue;
                }
                const float * logits = rows_ptr + (size_t) row * (size_t) n_vocab_;
                ++row;
                if (!logits) {
                    continue;
                }
                const size_t j = pos - first;
                float maxl = logits[0];
                for (int32_t v = 1; v < n_vocab_; ++v) {
                    if (logits[v] > maxl) {
                        maxl = logits[v];
                    }
                }
                double sum = 0.0;
                for (int32_t v = 0; v < n_vocab_; ++v) {
                    sum += std::exp((double) logits[v] - (double) maxl);
                }
                total += (double) logits[atoks[j]] - (double) maxl - std::log(sum);
                ++counted;
            }
        }
        if (!decoded) {
            if (err) *err = "scoring decode failed";
            return false;
        }
        if (counted == 0) {
            if (err) *err = "no logits for the answer tokens";
            return false;
        }
        nll = -total / (double) counted;
        tokens = counted;
        return true;
    };

    auto score_set = [&](const std::string & persona, const std::vector<VoicePair> & pairs,
                         VoiceScore & res) -> bool {
        res = VoiceScore();
        const int32_t repeat = std::max(1, spec.repeat);
        for (const VoicePair & pair : pairs) {
            const int32_t rounds = pair.answer.empty() ? repeat : 1;
            for (int32_t r = 0; r < rounds; ++r) {
                VoicePair sample = pair;
                if (sample.answer.empty()
                    && !make_answer(persona, sample.prompt, sample.answer, r)) {
                    return false;
                }
                double nll = 0.0;
                int32_t tokens = 0;
                if (!score_one(persona, sample, nll, tokens)) {
                    return false;
                }
                res.per_sample.push_back(nll);
                res.nll += nll;
                res.tokens += tokens;
                ++res.samples;
            }
        }
        if (res.samples > 0) {
            res.nll /= (double) res.samples;
        }
        return true;
    };

    std::vector<VoicePair> control = spec.negative;
    if (control.empty()) {
        control.reserve(spec.positive.size());
        for (const VoicePair & p : spec.positive) {
            control.push_back(VoicePair{ p.prompt, "" });
        }
    }

    if (!score_set(spec.persona, spec.positive, out.character_without)) {
        return false;
    }
    if (!score_set(neutral, control, out.control_without)) {
        return false;
    }

    const int32_t rc = llama_set_adapter_cvec(ctx, data.data(), data.size(), n_embd, 1, n_layers - 1);
    if (rc != 0) {
        if (err) *err = "failed to apply the control vector to the scoring context";
        return false;
    }

    if (!score_set(spec.persona, spec.positive, out.character_with)) {
        return false;
    }
    if (!score_set(neutral, control, out.control_with)) {
        return false;
    }

    out.character_delta = out.character_without.nll - out.character_with.nll;
    out.control_delta   = out.control_without.nll - out.control_with.nll;

    // The bar: the character's own lines have to become more likely, and the
    // plain assistant lines must not. A vector that lifts everything is a
    // generic tilt, not this character's voice.
    const double need = 0.02;
    const bool generic = out.control_delta > need;
    if (generic) {
        out.notes.push_back("plain assistant lines became more likely too: this is a generic tilt, not this character");
    }
    if (out.character_delta >= need) {
        out.notes.push_back("the character's own lines became more likely with the vector");
    } else if (out.character_delta > 0.0) {
        out.notes.push_back("the effect is there but small");
    } else {
        out.notes.push_back("the vector did not make the character's own lines more likely");
    }
    if (out.character_with.samples <= 1) {
        out.notes.push_back("only one authentic sample: the judgement is noisy");
    }
    if (out.character_delta >= need && !generic) {
        out.verdict = "pass";
    } else if (out.character_delta > 0.0 && !generic) {
        out.verdict = "weak";
    } else {
        out.verdict = "fail";
    }
    return true;
}

// Normalises a layer range the same way the apply path does, so the vector
// currently in place can be compared against a requested range.
static void normalize_cvec_layers(int32_t layer_start, int32_t layer_end, int32_t n_layers,
                                  int32_t * out_start, int32_t * out_end) {
    int32_t start = layer_start < 1 ? 1 : layer_start;
    int32_t end   = layer_end >= 0 ? layer_end : n_layers - 1;
    if (end > n_layers - 1) {
        end = n_layers - 1;
    }
    *out_start = start;
    *out_end   = end;
}

// Applies a vector to the live context. The caller holds the context lock.
bool Engine::set_control_vector_locked(const std::string & path, float scale,
                                        int32_t layer_start, int32_t layer_end,
                                        std::string * err) {
    const int32_t n_embd_model = llama_model_n_embd(model_);
    if (path.empty()) {
        // upstream disables the adapter when the data pointer is null
        const int32_t rc = llama_set_adapter_cvec(ctx_, nullptr, 0, n_embd_model, 0, -1);
        if (rc != 0) {
            *err = "failed to clear control vector";
            return false;
        }
        const bool changed = !cvec_cur_path_.empty();
        cvec_cur_path_.clear();
        std::fprintf(stderr, "[cvec] cleared%s\n", changed ? " (cache dropped)" : "");
        cvec_cur_scale_       = scale;
        cvec_cur_layer_start_ = layer_start;
        cvec_cur_layer_end_   = layer_end;
        if (changed) {
            clear_kv_cache();
        }
        return true;
    }
    std::ifstream f(path, std::ios::binary);
    if (!f) {
        *err = "cannot open control vector file: " + path;
        return false;
    }
    const int32_t n_embd = llama_model_n_embd(model_);
    const int32_t n_layers = llama_model_n_layer(model_);
    if (n_layers < 2) {
        *err = "control vector needs at least 2 layers";
        return false;
    }

    // Upstream layout (llama_adapter_cvec::apply): the buffer holds one vector
    // per model layer, but layer 0 has no tensor and is skipped. The offset for
    // layer il is n_embd * (il - 1), so the file holds n_layers - 1 vectors:
    // segment 0 -> model layer 1, segment il-1 -> model layer il.
    const size_t n_segments = static_cast<size_t>(n_layers - 1);
    const size_t want = static_cast<size_t>(n_embd) * n_segments;

    std::ifstream size_check(path, std::ios::binary | std::ios::ate);
    const std::streamoff file_bytes = size_check.tellg();
    if (file_bytes < 0 || static_cast<size_t>(file_bytes) != want * sizeof(float)) {
        char msg[256];
        std::snprintf(msg, sizeof(msg),
            "control vector file size mismatch: expected %zu float32 (n_embd * (n_layers - 1) = %d * %d), got %lld",
            want, n_embd, n_layers - 1, static_cast<long long>(file_bytes));
        *err = msg;
        return false;
    }

    std::vector<float> data(want, 0.0f);
    f.read(reinterpret_cast<char *>(data.data()), static_cast<std::streamsize>(want * sizeof(float)));
    if (static_cast<size_t>(f.gcount()) != want * sizeof(float)) {
        *err = "control vector file truncated while reading";
        return false;
    }

    // Scale is applied here, not upstream: llama_set_adapter_cvec has no
    // strength argument, it writes the buffer verbatim into the per-layer
    // tensors. Applying the scale before the call keeps the file on disk
    // unscaled (reusable at different strengths).
    if (scale != 1.0f) {
        for (float & v : data) {
            v *= scale;
        }
    }

    // Layer range is inclusive on both ends. Layer 0 has no control tensor
    // upstream, so the range must start at 1.
    int32_t il_start = layer_start;
    if (il_start < 1) {
        il_start = 1;
    }
    int32_t il_end = layer_end >= 0 ? layer_end : n_layers - 1;
    if (il_end > n_layers - 1) {
        il_end = n_layers - 1;
    }
    if (il_end < il_start) {
        *err = "control vector layer range is empty";
        return false;
    }

    int32_t rc = llama_set_adapter_cvec(ctx_, data.data(), data.size(), n_embd, il_start, il_end);
    if (rc != 0) {
        *err = "failed to apply control vector";
        return false;
    }
    // The KV cache was computed under the previous vector: its keys and
    // values carry that contribution, so they have to go.
    const bool changed = !(path == cvec_cur_path_ && scale == cvec_cur_scale_
                         && il_start == cvec_cur_layer_start_ && il_end == cvec_cur_layer_end_);
    cvec_cur_path_        = path;
    cvec_cur_scale_       = scale;
    cvec_cur_layer_start_ = il_start;
    cvec_cur_layer_end_   = il_end;
    if (changed) {
        clear_kv_cache();
    }
    std::fprintf(stderr,
        "[cvec] applied: %zu float32, layers %d..%d, scale %.3f%s\n",
        want, il_start, il_end, scale, changed ? " (cache dropped)" : "");
    return true;
}

// Drops the whole KV cache together with the slot bookkeeping that goes
// with it. Called when the control vector changes.
void Engine::clear_kv_cache() {
    llama_memory_t mem = llama_get_memory(ctx_);
    if (mem) {
        llama_memory_clear(mem, true);
    }
    for (Slot & s : slots_) {
        s.tokens.clear();
        s.checkpoints.clear();
        s.n_prompt = 0;
        s.n_decoded = 0;
        s.n_reused = 0;
    }
}

int32_t Engine::n_ctx_seq() const {
    return ctx_ ? static_cast<int32_t>(llama_n_ctx_seq(ctx_)) : 0;
}

std::string Engine::render_chatml(const std::vector<ChatMsg> & msgs, bool add_generation_prompt) const {
    std::vector<llama_chat_message> chat;
    chat.reserve(msgs.size());
    for (const ChatMsg & m : msgs) {
        chat.push_back(llama_chat_message{ m.role.c_str(), m.content.c_str() });
    }
    size_t cap = 256;
    for (const ChatMsg & m : msgs) cap += 2 * (m.content.size() + 64);
    std::vector<char> buf(cap, 0);
    int32_t n = llama_chat_apply_template("chatml", chat.data(), chat.size(), add_generation_prompt,
                                           buf.data(), static_cast<int32_t>(buf.size()));
    if (n > 0 && static_cast<size_t>(n) <= buf.size()) {
        return std::string(buf.data(), static_cast<size_t>(n));
    }
    // fallback: plain chatml
    std::string out;
    for (const ChatMsg & m : msgs) {
        out += "<|im_start|>";
        out += m.role;
        out += "\n";
        out += m.content;
        out += "<|im_end|>\n";
    }
    if (add_generation_prompt) {
        out += "<|im_start|>assistant\n";
    }
    return out;
}

std::string Engine::generation_prompt(bool enable_thinking) const {
    if (template_mode_ == "chatml") {
        return "<|im_start|>assistant\n";
    }
    return std::string("<|im_start|>assistant\n") +
           (enable_thinking ? "<think>\n" : "<think>\n\n</think>\n\n");
}

std::string Engine::render_prompt(const std::vector<ChatMsg> & msgs,
                                  bool add_generation_prompt,
                                  bool enable_thinking) const {
    return render_prompt(msgs, add_generation_prompt, enable_thinking, nullptr);
}

std::string Engine::render_prompt(const std::vector<ChatMsg> & msgs,
                                  bool add_generation_prompt,
                                  bool enable_thinking,
                                  std::vector<size_t> * user_offsets) const {
    if (template_mode_ == "chatml") {
        return render_chatml(msgs, add_generation_prompt);
    }
    return render_qwen35(msgs, add_generation_prompt, enable_thinking, user_offsets);
}

// ---------------------------------------------------------------------------
// Context checkpoints
//
// A recurrent (SSM) state cannot be rewound, so a diverging prompt is handled
// by restoring a saved state instead of trimming the KV cache. This mirrors
// the checkpoint mechanism of llama.cpp's own server.
// ---------------------------------------------------------------------------

bool Engine::save_checkpoint(Slot & slot, int32_t n_tokens, llama_pos pos_min, llama_pos pos_max, bool force) {
    if (cfg_.n_ctx_checkpoints <= 0) {
        return false;
    }
    if (!slot.checkpoints.empty()) {
        const int32_t last = slot.checkpoints.back().n_tokens;
        if (n_tokens <= last) {
            // the new checkpoint replaces the ones behind it
            while (!slot.checkpoints.empty() && slot.checkpoints.back().n_tokens >= n_tokens) {
                slot.checkpoints.pop_back();
            }
        } else if (!force && n_tokens - last < cfg_.checkpoint_min_step) {
            return false;
        }
    }
    const size_t size = llama_state_seq_get_size(ctx_, slot.seq_id);
    if (size == 0) {
        std::printf("[ckpt] state size is zero\n");
        std::fflush(stdout);
        return false;
    }
    Checkpoint ck;
    ck.n_tokens = n_tokens;
    ck.pos_min = pos_min;
    ck.pos_max = pos_max;
    ck.data.resize(size);
    const size_t written = llama_state_seq_get_data_ext(ctx_, ck.data.data(), size, slot.seq_id,
                                                           LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY);
    if (written == 0) {
        std::printf("[ckpt] save failed (size=%zu)\n", size);
        std::fflush(stdout);
        return false;
    }
    std::printf("[ckpt] saved n=%d size=%zu written=%zu\n", n_tokens, size, written);
    std::fflush(stdout);
    ck.data.resize(written);
    slot.checkpoints.push_back(std::move(ck));

    // keep the number of checkpoints bounded
    while (static_cast<int32_t>(slot.checkpoints.size()) > cfg_.n_ctx_checkpoints) {
        slot.checkpoints.erase(slot.checkpoints.begin());
    }
    return true;
}

int32_t Engine::restore_checkpoint(Slot & slot, int32_t max_tokens) {
    int best = -1;
    for (size_t i = 0; i < slot.checkpoints.size(); ++i) {
        if (slot.checkpoints[i].n_tokens <= max_tokens) {
            best = static_cast<int>(i);
        }
    }
    if (best < 0) {
        return -1;
    }
    const Checkpoint & ck = slot.checkpoints[static_cast<size_t>(best)];
    const size_t rc = llama_state_seq_set_data_ext(ctx_, ck.data.data(), ck.data.size(), slot.seq_id,
                                                      LLAMA_STATE_SEQ_FLAGS_PARTIAL_ONLY);
    if (rc == 0) {
        std::printf("[ckpt] restore failed (n=%d bytes=%zu)\n", ck.n_tokens, ck.data.size());
        std::fflush(stdout);
        return -1;
    }
    std::printf("[ckpt] restored n=%d\n", ck.n_tokens);
    std::fflush(stdout);
    return ck.n_tokens;
}

bool Engine::tokenize(const std::string & text, std::vector<llama_token> & out,
                      bool add_special, bool parse_special, std::string * err) const {
    if (!vocab_) {
        if (err) *err = "no vocab";
        return false;
    }
    const int32_t text_len = static_cast<int32_t>(text.size());
    int32_t n = llama_tokenize(vocab_, text.data(), text_len, nullptr, 0, add_special, parse_special);
    if (n < 0) {
        n = -n;
    }
    out.assign(static_cast<size_t>(n), 0);
    if (n > 0) {
        int32_t rc = llama_tokenize(vocab_, text.data(), text_len, out.data(), n, add_special, parse_special);
        if (rc < 0) {
            if (err) *err = "tokenize failed";
            return false;
        }
        out.resize(static_cast<size_t>(rc));
    }
    return true;
}

bool Engine::detokenize(const std::vector<llama_token> & tokens, std::string & out) const {
    out.clear();
    if (!vocab_) return false;
    char buf[512];
    for (llama_token t : tokens) {
        int32_t n = llama_token_to_piece(vocab_, t, buf, sizeof(buf), 0, false);
        if (n < 0) {
            n = llama_token_to_piece(vocab_, t, buf, sizeof(buf), 0, true);
        }
        if (n > 0) out.append(buf, static_cast<size_t>(n));
    }
    return true;
}

int Engine::pick_slot(const std::vector<llama_token> & tokens) const {
    int best = 0;
    size_t best_common = 0;
    bool have_best = false;
    for (size_t i = 0; i < slots_.size(); ++i) {
        const Slot & s = slots_[i];
        if (s.busy) continue;
        size_t common = 0;
        while (common < s.tokens.size() && common < tokens.size() && s.tokens[common] == tokens[common]) {
            ++common;
        }
        if (!have_best || common > best_common) {
            have_best = true;
            best = static_cast<int>(i);
            best_common = common;
        }
    }
    return best;
}

bool Engine::generate(const PromptInput & prompt, const SamplingParams & sp,
                      const TokenCallback & on_token, GenResult & res, std::string * err) {
    return generate_with_control_vector(prompt, sp, on_token, res, "", 1.0f, 1, -1, err);
}

// Runs one completion with the vector the request asks for.
//
// The vector is swapped only when it differs from the one currently applied.
// The same character asks for the same vector every turn, so the KV cache
// stays valid and the prompt prefix is still reused; switching to another
// character swaps the vector and drops the cache.
bool Engine::generate_with_control_vector(const PromptInput & prompt, const SamplingParams & sp,
                                          const TokenCallback & on_token, GenResult & res,
                                          const std::string & path, float scale,
                                          int32_t layer_start, int32_t layer_end,
                                          std::string * err) {
    std::lock_guard<std::mutex> lock(ctx_mutex_);

    const bool has_own = !path.empty();
    const std::string want_path = has_own ? path : cvec_default_path_;
    const float want_scale       = has_own ? scale : cvec_default_scale_;
    int32_t want_start = 0;
    int32_t want_end   = 0;
    normalize_cvec_layers(has_own ? layer_start : cvec_default_layer_start_,
                          has_own ? layer_end : cvec_default_layer_end_,
                          n_layers_, &want_start, &want_end);
    const bool same = want_path == cvec_cur_path_ && want_scale == cvec_cur_scale_
                   && want_start == cvec_cur_layer_start_ && want_end == cvec_cur_layer_end_;
    if (!same) {
        if (!set_control_vector_locked(want_path, want_scale, want_start, want_end, err)) {
            return false;
        }
    }
    {
        std::lock_guard<std::mutex> lock(counters_mutex_);
        counters_.requests += 1;
    }
    const bool ok = generate_locked(prompt, sp, on_token, res, err);
    {
        std::lock_guard<std::mutex> lock(counters_mutex_);
        if (!ok) {
            counters_.errors += 1;
        } else {
            counters_.prompt_tokens += res.prompt_tokens;
            counters_.completion_tokens += res.completion_tokens;
            counters_.t_prompt_ms += res.t_prompt_ms;
            counters_.t_gen_ms += res.t_gen_ms;
            if (res.finish_reason == "aborted") {
                counters_.aborted += 1;
            }
        }
    }
    return ok;
}

Engine::Counters Engine::counters() const {
    std::lock_guard<std::mutex> lock(counters_mutex_);
    return counters_;
}

// Runs one completion. The caller holds the context lock.
bool Engine::generate_locked(const PromptInput & prompt, const SamplingParams & sp,
                             const TokenCallback & on_token, GenResult & res,
                             std::string * err) {
    std::vector<llama_token> tokens;
    if (!tokenize(prompt.full, tokens, true, false, err)) return false;
    if (tokens.empty()) {
        if (err) *err = "empty prompt";
        return false;
    }

    // the cacheable part has to be a token prefix of the full prompt
    std::vector<llama_token> cache_tokens;
    if (!prompt.cacheable.empty()) {
        std::vector<llama_token> probe;
        if (tokenize(prompt.cacheable, probe, true, false, nullptr)) {
            bool is_prefix = probe.size() <= tokens.size();
            for (size_t i = 0; is_prefix && i < probe.size(); ++i) {
                if (probe[i] != tokens[i]) is_prefix = false;
            }
            if (is_prefix) {
                cache_tokens = probe;
            }
        }
    }
    if (cache_tokens.empty()) {
        // raw prompts: keep the last token out of the cache, so that the next
        // request always evaluates at least one token and gets fresh logits
        cache_tokens.assign(tokens.begin(), tokens.end() > tokens.begin() ? tokens.end() - 1 : tokens.end());
    }

    const int32_t seq_max = n_ctx_seq();
    int32_t max_tokens = sp.max_tokens;
    if (max_tokens < 0) {
        max_tokens = seq_max - static_cast<int32_t>(tokens.size());
        if (max_tokens < 0) max_tokens = 0;
    }
    if (static_cast<int32_t>(tokens.size()) > seq_max) {
        if (err) *err = "prompt exceeds the context window";
        return false;
    }
    if (static_cast<int32_t>(tokens.size()) + max_tokens > seq_max) {
        max_tokens = seq_max - static_cast<int32_t>(tokens.size());
        res.finish_reason = "length";
    }

    const int slot_id = pick_slot(tokens);
    Slot & slot = slots_[static_cast<size_t>(slot_id)];
    slot.busy = true;
    res.slot_id = slot_id;

    llama_memory_t mem = llama_get_memory(ctx_);

    // ---- KV prefix reuse -------------------------------------------------
    // The memory still holds the tokens of the previous prompt. When the new
    // prompt continues it, only the new tail has to be decoded. When it
    // diverges, the cache is rewound - and if the recurrent state of a
    // hybrid model refuses to rewind, a context checkpoint is restored.
    size_t common = 0;
    while (common < slot.tokens.size() && common < tokens.size() && slot.tokens[common] == tokens[common]) {
        ++common;
    }
    int32_t n_past = static_cast<int32_t>(common);

    // The checkpoints stay in the list: a checkpoint that reaches beyond the
    // shared prefix is simply not a candidate for the restore.
    std::printf("[kv] request common=%d cached=%zu ckpts=%zu\n", n_past, slot.tokens.size(), slot.checkpoints.size());
    std::fflush(stdout);

    if (n_past < static_cast<int32_t>(slot.tokens.size())) {
        // The cached prompt diverges. Rewinding the recurrent state of a
        // hybrid model is not reliable, so a saved state is restored instead.
        const int32_t restored = restore_checkpoint(slot, n_past);
        if (restored >= 0) {
            n_past = restored;
            // the checkpoint covers the shared prefix, so the cached tokens
            // are the first tokens of the new prompt
            slot.tokens.assign(tokens.begin(), tokens.begin() + restored);
            std::printf("[kv] restored checkpoint -> %d tokens\n", n_past);
        } else {
            llama_memory_seq_rm(mem, slot.seq_id, 0, -1);
            slot.tokens.clear();
            slot.checkpoints.clear();
            n_past = 0;
            std::printf("[kv] diverged, no checkpoint, full reset\n");
        }
        std::fflush(stdout);
    }

    // checkpoints behind the shared prefix belong to another prompt
    while (!slot.checkpoints.empty() && slot.checkpoints.back().n_tokens > n_past) {
        slot.checkpoints.pop_back();
    }

    // The whole prompt is cached but the logits of the last position are
    // stale, so the prompt is evaluated again from scratch.
    if (n_past == static_cast<int32_t>(tokens.size()) && n_past > 0) {
        llama_memory_seq_rm(mem, slot.seq_id, 0, -1);
        slot.tokens.clear();
        n_past = 0;
        std::printf("[kv] prompt fully cached, re-evaluated\n");
        std::fflush(stdout);
    }
    const size_t start = static_cast<size_t>(n_past);
    slot.n_reused = n_past;
    res.n_prompt_reused = n_past;

    // map the checkpoint offsets (character positions) to token indices
    std::vector<int32_t> ckpt_tokens;
    if (cfg_.n_ctx_checkpoints > 0) {
        for (size_t off : prompt.checkpoint_offsets) {
            if (off == 0 || off >= prompt.full.size()) continue;
            std::vector<llama_token> prefix;
            if (!tokenize(prompt.full.substr(0, off), prefix, true, false, nullptr)) continue;
            if (prefix.size() > tokens.size()) continue;
            bool matches = true;
            for (size_t i = 0; i < prefix.size(); ++i) {
                if (prefix[i] != tokens[i]) { matches = false; break; }
            }
            if (!matches) continue;
            ckpt_tokens.push_back(static_cast<int32_t>(prefix.size()));
        }
        std::sort(ckpt_tokens.begin(), ckpt_tokens.end());
        ckpt_tokens.erase(std::unique(ckpt_tokens.begin(), ckpt_tokens.end()), ckpt_tokens.end());
    }

    // decode the part of the prompt that is not cached yet
    const double t_prefill_start = now_ms();
    {
        const int32_t chunk_max = std::max(1, std::min(cfg_.n_batch, cfg_.n_ubatch));
        llama_batch batch = llama_batch_init(chunk_max, 0, 1);
        bool failed = false;
        for (size_t off = start; off < tokens.size() && !failed; ) {
            // stop at the next checkpoint boundary so the state can be saved there
            size_t limit = tokens.size();
            for (int32_t b : ckpt_tokens) {
                if (b > static_cast<int32_t>(off) && b < static_cast<int32_t>(limit)) {
                    limit = static_cast<size_t>(b);
                }
            }
            const size_t n = std::min<size_t>(static_cast<size_t>(chunk_max), limit - off);
            batch.n_tokens = static_cast<int32_t>(n);
            for (size_t i = 0; i < n; ++i) {
                batch.token[i] = tokens[off + i];
                batch.pos[i] = static_cast<llama_pos>(off + i);
                batch.n_seq_id[i] = 1;
                batch.seq_id[i][0] = slot.seq_id;
                batch.logits[i] = 0;
            }
            const bool last_chunk = (off + n == tokens.size());
            if (last_chunk) {
                batch.logits[n - 1] = 1;
            }
            const int32_t rc = llama_decode(ctx_, batch);
            if (rc != 0) {
                if (err) {
                    *err = rc == 1 ? "no free slot in the KV cache" : "llama_decode failed";
                }
                failed = true;
                break;
            }
            off += n;
            for (int32_t b : ckpt_tokens) {
                if (b == static_cast<int32_t>(off)) {
                    save_checkpoint(slot, b,
                                   llama_memory_seq_pos_min(mem, slot.seq_id),
                                   llama_memory_seq_pos_max(mem, slot.seq_id));
                }
            }
        }
        llama_batch_free(batch);
        if (failed) {
            slot.busy = false;
            return false;
        }
    }
    const double t_prefill_end = now_ms();
    slot.t_prompt_ms = t_prefill_end - t_prefill_start;
    slot.n_prompt = static_cast<int32_t>(tokens.size());

    // a checkpoint at the end of the prompt: the next request can return to
    // exactly this state without touching the recurrent state
    if (cfg_.n_ctx_checkpoints > 0) {
        save_checkpoint(slot, static_cast<int32_t>(tokens.size()),
                        llama_memory_seq_pos_min(mem, slot.seq_id),
                        llama_memory_seq_pos_max(mem, slot.seq_id), true);
    }

    // generation
    llama_sampler * smpl = build_sampler(vocab_, n_vocab_, sp);
    StreamSplitter splitter(sp.stop, on_token);
    const double t_gen_start = now_ms();
    int32_t n_decoded = 0;
    llama_pos pos = static_cast<llama_pos>(tokens.size());
    bool eog = false;
    llama_batch one = llama_batch_init(1, 0, 1);

    while (max_tokens < 0 || n_decoded < max_tokens) {
        const llama_token id = llama_sampler_sample(smpl, ctx_, -1);
        if (llama_vocab_is_eog(vocab_, id)) {
            eog = true;
            break;
        }
        char buf[256];
        int32_t n = llama_token_to_piece(vocab_, id, buf, sizeof(buf), 0, false);
        if (n > 0) {
            splitter.feed(std::string(buf, static_cast<size_t>(n)));
        }
        if (splitter.stop_hit()) break;
        if (splitter.aborted()) {
            // the client went away: stop decoding instead of burning the GPU
            std::fprintf(stderr, "[gen] aborted: client gone\n");
            res.finish_reason = "aborted";
            break;
        }

        // feed the sampled token back into the cache
        one.n_tokens = 1;
        one.token[0] = id;
        one.pos[0] = pos;
        one.n_seq_id[0] = 1;
        one.seq_id[0][0] = slot.seq_id;
        one.logits[0] = 1;
        const int32_t rc = llama_decode(ctx_, one);
        ++pos;
        ++n_decoded;
        if (rc != 0) {
            break;
        }
    }
    const double t_gen_end = now_ms();

    // 撞到输出上限时如实标成 length：上游 llama.cpp server 也这么回，
    // 客户端据此判断这一轮是被截断而不是自然收尾。
    if (!eog && !splitter.stop_hit() && !splitter.aborted()
        && max_tokens >= 0 && n_decoded >= max_tokens) {
        res.finish_reason = "length";
    }

    splitter.flush();
    llama_sampler_free(smpl);
    llama_batch_free(one);

    // go back to the state at the end of the cacheable part (the messages
    // without the generation prompt), so the next request continues from there
    const int32_t restored = restore_checkpoint(slot, static_cast<int32_t>(cache_tokens.size()));
    if (restored >= 0) {
        slot.tokens.assign(tokens.begin(), tokens.begin() + restored);
        std::printf("[kv] cache kept at %d tokens\n", restored);
    } else {
        llama_memory_seq_rm(mem, slot.seq_id, 0, -1);
        slot.tokens.clear();
        std::printf("[kv] no checkpoint, cache reset\n");
    }
    std::fflush(stdout);
    slot.n_decoded = n_decoded;
    slot.t_gen_ms = t_gen_end - t_gen_start;
    slot.busy = false;

    res.content = splitter.content();
    res.reasoning = splitter.reasoning();
    res.prompt_tokens = static_cast<int32_t>(tokens.size());
    res.completion_tokens = n_decoded;
    res.t_prompt_ms = slot.t_prompt_ms;
    res.t_gen_ms = slot.t_gen_ms;
    res.t_total_ms = (t_gen_end - t_prefill_start);
    if (!eog && res.finish_reason != "length" && n_decoded >= max_tokens && max_tokens >= 0) {
        res.finish_reason = "length";
    }
    return true;
}

std::vector<Engine::SlotInfo> Engine::slots_info() const {
    std::lock_guard<std::mutex> lock(ctx_mutex_);
    std::vector<SlotInfo> out;
    for (size_t i = 0; i < slots_.size(); ++i) {
        const Slot & s = slots_[i];
        SlotInfo info;
        info.id = static_cast<int32_t>(i);
        info.seq_id = static_cast<int32_t>(s.seq_id);
        info.busy = s.busy;
        info.n_cached = static_cast<int32_t>(s.tokens.size());
        info.n_prompt = s.n_prompt;
        info.n_decoded = s.n_decoded;
        info.n_reused = s.n_reused;
        info.t_prompt_ms = s.t_prompt_ms;
        info.t_gen_ms = s.t_gen_ms;
        out.push_back(info);
    }
    return out;
}

} // namespace wwc
