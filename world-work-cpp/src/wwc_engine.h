// Inference engine: model, context, slots and KV prefix reuse.
//
// Design follows llama.cpp's own server: one context holds one KV cache, and
// every slot owns a sequence id inside it. A slot remembers the prompt tokens
// it last decoded; when the next request shares a prefix with it, the cached
// KV cells are reused and only the differing tail is decoded again.
#pragma once

#include "llama.h"

#include <functional>
#include <mutex>
#include <string>
#include <vector>

#include "wwc_template.h"

namespace wwc {

struct SamplingParams {
    int32_t  max_tokens        = -1;   // -1 = unlimited
    float    temperature       = 0.8f;
    float    top_p             = 0.95f;
    int32_t  top_k             = 40;
    float    min_p             = 0.05f;
    float    typical_p         = 1.0f;
    float    repeat_penalty    = 1.1f;
    float    presence_penalty  = 0.0f;
    float    frequency_penalty = 0.0f;
    int32_t  repeat_last_n     = 64;
    uint32_t seed              = LLAMA_DEFAULT_SEED;
    bool     has_seed          = false;
    float    dynatemp_range    = 0.0f;
    float    dynatemp_exponent = 1.0f;
    float    xtc_probability   = 0.0f;
    float    xtc_threshold     = 0.1f;
    // DRY: blocks the degenerate repetition of a phrase. The upstream server
    // has it, so the project should not lose it by switching servers.
    float    dry_multiplier    = 0.0f;
    float    dry_base          = 1.75f;
    int32_t  dry_allowed_length = 2;
    int32_t  dry_penalty_last_n = -1;
    // Mirostat: 0 off, 1 the 1.0 algorithm, 2 the 2.0 one.
    int32_t  mirostat          = 0;
    float    mirostat_tau      = 5.0f;
    float    mirostat_eta      = 0.1f;
    float    top_n_sigma       = 0.0f;
    uint32_t min_keep          = 1;
    // GBNF grammar constraining the output. Empty means no constraint.
    std::string grammar;
    std::vector<std::string>  stop;
};

// A prompt plus the part of it that may stay in the KV cache.
//
// llama.cpp keeps the "messages" part of a prompt in the cache and re-renders
// the generation prompt on every request. For hybrid models the recurrent
// state cannot be rewound, so the cached part has to be a prefix of the next
// prompt - caching only the messages part guarantees that in a chat.
struct PromptInput {
    std::string full;       // everything that is fed to the model
    std::string cacheable;  // the prefix that may stay in the cache
    // character offsets in "full" where a context checkpoint should be taken
    std::vector<size_t> checkpoint_offsets;
};

struct GenResult {
    std::string content;
    std::string reasoning;
    std::string finish_reason = "stop";
    int32_t prompt_tokens     = 0;
    int32_t completion_tokens  = 0;
    int32_t n_prompt_reused    = 0;
    int32_t slot_id            = 0;
    double  t_prompt_ms        = 0.0;
    double  t_gen_ms           = 0.0;
    double  t_total_ms         = 0.0;
};

// Called for every generated text fragment. is_reasoning marks the part that
// lives inside a <think> block.
//
// Returning false stops the generation. The client is gone and there is no
// point in decoding the rest of the reply: the GPU would keep working on an
// answer nobody will read, and the next request would queue behind it.
using TokenCallback = std::function<bool(const std::string & text, bool is_reasoning)>;

class Engine {
public:
    struct Config {
        std::string model_path;
        int32_t  n_ctx         = 16384;
        int32_t  n_gpu_layers  = 999;
        int32_t  n_parallel    = 1;
        int32_t  n_threads     = 0;
        int32_t  n_threads_batch = 0;
        int32_t  n_batch       = 512;
        int32_t  n_ubatch      = 512;
        // number of recurrent state snapshots kept per sequence; this is what
        // makes a prefix rewind possible on hybrid (attention + SSM) models
        int32_t  n_rs_seq      = 4;
        std::string cache_type_k = "q8_0";
        std::string cache_type_v = "q8_0";
        bool     flash_attn    = false;
        bool     use_mmap      = true;
        std::string lora_path;
        float    lora_scale    = 1.0f;
        std::string cvec_path;
        float    cvec_scale    = 1.0f;
        // layer 0 has no control tensor upstream, so the default start is 1
        int32_t  cvec_layer_start = 1;
        int32_t  cvec_layer_end   = -1;  // -1 means n_layers - 1
        std::string chat_template_mode = "auto";  // auto | qwen35 | chatml
        // max number of context checkpoints kept per slot (0 disables them)
        int32_t  n_ctx_checkpoints = 4;
        // minimum spacing between two checkpoints, in tokens
        int32_t  checkpoint_min_step = 0;
        bool     verbose       = false;
    };

    explicit Engine(const Config & cfg);
    ~Engine();

    bool load(std::string * err);
    void unload();

    // Swaps the model file without restarting the process. The upstream
    // server can only change models by being restarted; here the weights
    // are reloaded in place so a different GGUF on disk can be tried
    // without dropping the connection.
    bool reload_model(const std::string & path, std::string * err);

    const Config & config() const { return cfg_; }
    const llama_model * model() const { return model_; }
    const llama_vocab * vocab() const { return vocab_; }
    int32_t n_vocab() const { return n_vocab_; }
    int32_t n_ctx_train() const { return n_ctx_train_; }
    int32_t n_embd() const { return n_embd_; }
    int32_t n_layers() const { return n_layers_; }
    int32_t n_ctx_seq() const;
    const std::string & model_desc() const { return model_desc_; }
    const std::string & chat_template_raw() const { return chat_template_; }
    const std::string & template_mode() const { return template_mode_; }

    // Renders messages into the model prompt.
    std::string render_prompt(const std::vector<ChatMsg> & msgs,
                               bool add_generation_prompt,
                               bool enable_thinking) const;

    // Same, but also reports the character offsets of the user turns.
    std::string render_prompt(const std::vector<ChatMsg> & msgs,
                               bool add_generation_prompt,
                               bool enable_thinking,
                               std::vector<size_t> * user_offsets) const;

    // The generation prompt that render_prompt appends.
    std::string generation_prompt(bool enable_thinking) const;

    // Swaps the control vector of the running context. The file holds
    // n_embd float32 per layer, layer 0 omitted (n_layers - 1 segments).
    // An empty path clears the vector.
    bool set_control_vector(const std::string & path, float scale,
                             int32_t layer_start, int32_t layer_end,
                             std::string * err);

    // Which vector is in place right now, and where it came from.
    //
    // The reply has to say this: a client that only sees "applied: false"
    // cannot tell "no voice at all" from "the server default voice was in
    // place", and those two produce different text.
    struct ControlVectorState {
        std::string path;
        float    scale       = 1.0f;
        int32_t  layer_start = 1;
        int32_t  layer_end   = -1;
        bool     active      = false;  // a vector is in place
        bool     is_default  = false;  // the one in place is the server default
    };
    ControlVectorState control_vector_state();

    // A prompt/answer pair used to build a voice vector.
    struct VoicePair {
        std::string prompt;  // what the user says
        std::string answer;  // what the character says back; empty: generate one
    };

    // Everything needed to build a voice vector for one character.
    //
    // The recipe is the proven one: run the same prompts twice, once with the
    // character's own voice and once with a plain assistant, compare the
    // residual streams over the answer tokens, and scale the difference to a
    // fixed fraction of the residual norm.
    struct VoiceSpec {
        std::string name;             // for the log
        std::string persona;          // the character's own voice, as a system prompt
        std::string neutral_persona;  // the plain assistant, as a system prompt
        std::vector<VoicePair> positive;
        // the plain side; empty means "the same prompts with the neutral persona"
        std::vector<VoicePair> negative;
        // Paired construction: the plain side reads the same words the
        // character side read, only the persona differs.
        //
        // Measured on four live cards it is the wrong difference: it captures
        // what the persona means (identity, topic), not how she talks, and a
        // vector built from it makes the author's own lines LESS likely. The
        // default construction keeps the plain side writing its own answer,
        // and that one measures the voice.
        bool paired = false;
        float ratio = 0.02f;          // injection size relative to the residual
        int32_t repeat = 2;           // samples generated per prompt that has no answer
        int32_t max_tokens = 96;      // used when an answer has to be generated
        int32_t capture_ctx = 2048;   // context size of the capture context
    };

    struct VoiceVector {
        std::vector<float> data;             // (n_layers - 1) * n_embd float32
        std::vector<double> residual_norms;  // per layer
        std::vector<double> factors;         // per layer
        float ratio = 0.0f;
        int32_t n_pairs = 0;
    };

    // Builds a control vector for a character at runtime.
    //
    // The capture runs in its own context so the serving context keeps its
    // KV cache; the GPU is shared, so other requests wait meanwhile.
    bool generate_voice_vector(const VoiceSpec & spec, VoiceVector & out, std::string * err);

    // How well a voice vector fits the character it was built for.
    struct VoiceScore {
        double nll = 0.0;      // mean negative log likelihood per answer token
        int32_t tokens = 0;
        int32_t samples = 0;
        std::vector<double> per_sample;
    };

    struct VoiceVerifyResult {
        VoiceScore character_without;
        VoiceScore character_with;
        VoiceScore control_without;
        VoiceScore control_with;
        double character_delta = 0.0;  // nll_without - nll_with
        double control_delta   = 0.0;
        std::string verdict;           // pass | weak | fail
        std::vector<std::string> notes;
    };

    // Checks a voice vector against the character's own lines.
    //
    // The criterion is deterministic, not a judge's opinion: the negative log
    // likelihood of the character's own lines has to go down when the vector
    // is applied, while the likelihood of plain assistant lines must not go
    // up. A vector that only makes every sentence more likely is a generic
    // tilt, not a voice.
    bool verify_voice_vector(const VoiceSpec & spec, const std::string & vector_path,
                             float scale, VoiceVerifyResult & out, std::string * err);

    // Runs one completion with a vector that belongs to the request.
    //
    // A voice vector belongs to a character, so it travels with the request
    // instead of being pinned to the server for every character. An empty
    // path means "use the server default".
    //
    // The vector stays applied after the request: the same character asks for
    // the same vector every turn, so the KV cache stays valid and the prompt
    // prefix is still reused. Switching to another character swaps the
    // vector and drops the cache, because cached keys and values were
    // computed under the previous vector.
    bool generate_with_control_vector(const PromptInput & prompt,
                                      const SamplingParams & sp,
                                      const TokenCallback & on_token,
                                      GenResult & res,
                                      const std::string & path, float scale,
                                      int32_t layer_start, int32_t layer_end,
                                      std::string * err);

    // Loads or unloads a LoRA adapter at runtime. An empty path unloads
    // the current one. Switching the adapter changes the effective model,
    // so the KV cache is dropped with it.
    bool set_lora(const std::string & path, float scale, std::string * err);

    // Path of the adapter currently attached, empty when there is none.
    std::string lora_path() const;

    bool tokenize(const std::string & text, std::vector<llama_token> & out,
                  bool add_special, bool parse_special, std::string * err) const;
    bool detokenize(const std::vector<llama_token> & tokens, std::string & out) const;

    // Runs one completion. Serialised on the context mutex: the GPU is the
    // bottleneck, so requests queue instead of fighting over the cache.
    bool generate(const PromptInput & prompt, const SamplingParams & sp,
                  const TokenCallback & on_token, GenResult & res, std::string * err);

    struct SlotInfo {
        int32_t id          = 0;
        int32_t seq_id      = 0;
        bool    busy        = false;
        int32_t n_cached    = 0;
        int32_t n_prompt    = 0;
        int32_t n_decoded   = 0;
        int32_t n_reused    = 0;
        double  t_prompt_ms = 0.0;
        double  t_gen_ms    = 0.0;
    };
    std::vector<SlotInfo> slots_info() const;

    // Counters for the metrics endpoint.
    struct Counters {
        int64_t requests        = 0;
        int64_t errors          = 0;
        int64_t prompt_tokens   = 0;
        int64_t completion_tokens = 0;
        int64_t aborted         = 0;
        double  t_prompt_ms     = 0.0;
        double  t_gen_ms        = 0.0;
    };
    Counters counters() const;

private:
    // A saved state of one sequence. Recurrent (SSM) states cannot be
    // rewound, so a checkpoint is the only way to go back to an earlier point
    // of a hybrid model.
    struct Checkpoint {
        int32_t n_tokens = 0;
        llama_pos pos_min = -1;
        llama_pos pos_max = -1;
        std::vector<uint8_t> data;
    };

    struct Slot {
        llama_seq_id seq_id = 0;
        bool    busy = false;
        std::vector<llama_token> tokens;  // prompt tokens whose KV is still valid
        std::vector<Checkpoint> checkpoints;
        int32_t n_prompt = 0;
        int32_t n_decoded = 0;
        int32_t n_reused = 0;
        double  t_prompt_ms = 0.0;
        double  t_gen_ms = 0.0;
    };

    bool load_model(std::string * err);
    bool save_checkpoint(Slot & slot, int32_t n_tokens, llama_pos pos_min, llama_pos pos_max, bool force = false);
    int32_t restore_checkpoint(Slot & slot, int32_t max_tokens);
    bool load_context(std::string * err);
    bool apply_lora(std::string * err);
    bool apply_control_vector(std::string * err);
    bool set_control_vector_locked(const std::string & path, float scale,
                                   int32_t layer_start, int32_t layer_end,
                                   std::string * err);
    bool generate_locked(const PromptInput & prompt, const SamplingParams & sp,
                         const TokenCallback & on_token, GenResult & res,
                         std::string * err);
    // Drops the whole KV cache. Used when the control vector changes: the
    // cached keys and values were computed under the previous vector.
    void clear_kv_cache();
    int  pick_slot(const std::vector<llama_token> & tokens) const;
    std::string render_chatml(const std::vector<ChatMsg> & msgs, bool add_generation_prompt) const;

    Config cfg_;
    llama_model *   model_ = nullptr;
    llama_context * ctx_   = nullptr;
    const llama_vocab * vocab_ = nullptr;
    int32_t n_vocab_     = 0;
    int32_t n_ctx_train_ = 0;
    int32_t n_embd_      = 0;
    int32_t n_layers_    = 0;
    std::string model_desc_;
    std::string chat_template_;
    std::string template_mode_ = "auto";
    // The vector the server falls back to when a request carries none.
    std::string cvec_default_path_;
    float    cvec_default_scale_       = 1.0f;
    int32_t  cvec_default_layer_start_ = 1;
    int32_t  cvec_default_layer_end_   = -1;
    // The vector currently applied to the context.
    std::string cvec_cur_path_;
    float    cvec_cur_scale_       = 1.0f;
    int32_t  cvec_cur_layer_start_ = 1;
    int32_t  cvec_cur_layer_end_   = -1;
    std::vector<Slot> slots_;
    // The adapter currently attached, kept so it can be freed on unload.
    llama_adapter_lora * lora_ = nullptr;
    std::string lora_loaded_path_;
    mutable std::mutex ctx_mutex_;
    // Counters for the metrics endpoint.
    mutable std::mutex counters_mutex_;
    Counters counters_;
};

} // namespace wwc
