#include "wwc_server.h"

#include <chrono>
#include <cstdio>
#include <fstream>
#include <sstream>

namespace wwc {

namespace {

std::string now_iso8601() {
    const std::time_t t = std::time(nullptr);
    std::tm tm_utc{};
#ifdef _WIN32
    gmtime_s(&tm_utc, &t);
#else
    gmtime_r(&t, &tm_utc);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &tm_utc);
    return buf;
}

int64_t now_epoch() {
    return static_cast<int64_t>(std::chrono::system_clock::now().time_since_epoch().count() / 1000000);
}

std::string make_id(const char * prefix) {
    static int counter = 0;
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%s-%d-%d", prefix, static_cast<int>(now_epoch()), ++counter);
    return buf;
}

// OpenAI allows the content of a message to be a string or a list of parts.
std::string content_to_text(const Json & node) {
    if (node.is_string()) return node.as_str();
    if (!node.is_array()) return std::string();
    std::string out;
    for (const Json & part : node.items()) {
        if (part.is_string()) {
            out += part.as_str();
            continue;
        }
        if (part.is_object()) {
            const Json * text = part.get("text");
            if (text && text->is_string()) out += text->as_str();
        }
    }
    return out;
}

std::vector<ChatMsg> parse_messages(const Json & body) {
    std::vector<ChatMsg> msgs;
    const Json * arr = body.get("messages");
    if (!arr || !arr->is_array()) return msgs;
    for (const Json & m : arr->items()) {
        if (!m.is_object()) continue;
        ChatMsg cm;
        const Json * role = m.get("role");
        cm.role = role && role->is_string() ? role->as_str() : "user";
        const Json * content = m.get("content");
        cm.content = content ? content_to_text(*content) : std::string();
        const Json * reasoning = m.get("reasoning_content");
        if (reasoning && reasoning->is_string()) cm.reasoning = reasoning->as_str();
        msgs.push_back(cm);
    }
    return msgs;
}

void apply_sampling(const Json & body, SamplingParams & sp) {
    if (const Json * v = body.get("max_tokens")) sp.max_tokens = static_cast<int32_t>(v->as_int(sp.max_tokens));
    if (const Json * v = body.get("max_completion_tokens")) sp.max_tokens = static_cast<int32_t>(v->as_int(sp.max_tokens));
    if (const Json * v = body.get("temperature")) sp.temperature = static_cast<float>(v->as_double(sp.temperature));
    if (const Json * v = body.get("top_p")) sp.top_p = static_cast<float>(v->as_double(sp.top_p));
    if (const Json * v = body.get("top_k")) sp.top_k = static_cast<int32_t>(v->as_int(sp.top_k));
    if (const Json * v = body.get("min_p")) sp.min_p = static_cast<float>(v->as_double(sp.min_p));
    if (const Json * v = body.get("typical_p")) sp.typical_p = static_cast<float>(v->as_double(sp.typical_p));
    if (const Json * v = body.get("repeat_penalty")) sp.repeat_penalty = static_cast<float>(v->as_double(sp.repeat_penalty));
    if (const Json * v = body.get("presence_penalty")) sp.presence_penalty = static_cast<float>(v->as_double(sp.presence_penalty));
    if (const Json * v = body.get("frequency_penalty")) sp.frequency_penalty = static_cast<float>(v->as_double(sp.frequency_penalty));
    if (const Json * v = body.get("repeat_last_n")) sp.repeat_last_n = static_cast<int32_t>(v->as_int(sp.repeat_last_n));
    if (const Json * v = body.get("dynatemp_range")) sp.dynatemp_range = static_cast<float>(v->as_double(sp.dynatemp_range));
    if (const Json * v = body.get("dynatemp_exponent")) sp.dynatemp_exponent = static_cast<float>(v->as_double(sp.dynatemp_exponent));
    if (const Json * v = body.get("xtc_probability")) sp.xtc_probability = static_cast<float>(v->as_double(sp.xtc_probability));
    if (const Json * v = body.get("xtc_threshold")) sp.xtc_threshold = static_cast<float>(v->as_double(sp.xtc_threshold));
    if (const Json * v = body.get("dry_multiplier")) sp.dry_multiplier = static_cast<float>(v->as_double(0.0));
    if (const Json * v = body.get("dry_base")) sp.dry_base = static_cast<float>(v->as_double(1.75));
    if (const Json * v = body.get("dry_allowed_length")) sp.dry_allowed_length = static_cast<int32_t>(v->as_int(2));
    if (const Json * v = body.get("dry_penalty_last_n")) sp.dry_penalty_last_n = static_cast<int32_t>(v->as_int(-1));
    if (const Json * v = body.get("mirostat")) sp.mirostat = static_cast<int32_t>(v->as_int(0));
    if (const Json * v = body.get("mirostat_tau")) sp.mirostat_tau = static_cast<float>(v->as_double(5.0));
    if (const Json * v = body.get("mirostat_eta")) sp.mirostat_eta = static_cast<float>(v->as_double(0.1));
    if (const Json * v = body.get("top_n_sigma")) sp.top_n_sigma = static_cast<float>(v->as_double(0.0));
    if (const Json * v = body.get("min_keep")) sp.min_keep = static_cast<uint32_t>(v->as_int(1));
    if (const Json * v = body.get("grammar")) sp.grammar = v->as_str("");
    if (const Json * v = body.get("seed")) {
        const int64_t s = v->as_int(LLAMA_DEFAULT_SEED);
        sp.seed = static_cast<uint32_t>(s);
        sp.has_seed = true;
    }
    if (const Json * v = body.get("stop")) {
        if (v->is_string()) {
            if (!v->as_str().empty()) sp.stop.push_back(v->as_str());
        } else if (v->is_array()) {
            for (const Json & s : v->items()) {
                if (s.is_string() && !s.as_str().empty()) sp.stop.push_back(s.as_str());
            }
        }
    }
}

bool request_wants_stream(const Json & body) {
    const Json * v = body.get("stream");
    return v && v->as_bool(false);
}

bool request_enable_thinking(const Json & body, bool fallback) {
    const Json * v = body.get("enable_thinking");
    if (v && v->is_bool()) return v->as_bool(fallback);
    const Json * kwargs = body.get("chat_template_kwargs");
    if (kwargs && kwargs->is_object()) {
        const Json * e = kwargs->get("enable_thinking");
        if (e && e->is_bool()) return e->as_bool(fallback);
    }
    return fallback;
}

Json usage_json(const GenResult & res) {
    Json usage = Json::make_object();
    usage.set("prompt_tokens", Json::make_int(res.prompt_tokens));
    usage.set("completion_tokens", Json::make_int(res.completion_tokens));
    usage.set("total_tokens", Json::make_int(res.prompt_tokens + res.completion_tokens));
    // OpenAI standard field: prompt tokens served from the KV prefix cache.
    // The backend adapter reads this to report prefix reuse; without it the
    // reuse count is silently reported as zero even when the cache hit.
    usage.set("cached_tokens", Json::make_int(res.n_prompt_reused));
    return usage;
}

Json timings_json(const GenResult & res) {
    Json t = Json::make_object();
    t.set("prompt_ms", Json::make_number(res.t_prompt_ms));
    t.set("gen_ms", Json::make_number(res.t_gen_ms));
    t.set("total_ms", Json::make_number(res.t_total_ms));
    if (res.t_prompt_ms > 0.0) {
        t.set("prompt_per_second", Json::make_number(res.prompt_tokens * 1000.0 / res.t_prompt_ms));
    }
    if (res.t_gen_ms > 0.0) {
        t.set("gen_per_second", Json::make_number(res.completion_tokens * 1000.0 / res.t_gen_ms));
    }
    t.set("n_prompt_reused", Json::make_int(res.n_prompt_reused));
    t.set("slot_id", Json::make_int(res.slot_id));
    return t;
}

// Reads the prompt/answer pairs of a voice request.
void read_voice_pairs(const Json & body, const char * key,
                      std::vector<Engine::VoicePair> & out) {
    const Json * arr = body.get(key);
    if (!arr || !arr->is_array()) {
        return;
    }
    for (const Json & item : arr->items()) {
        if (!item.is_object()) {
            continue;
        }
        Engine::VoicePair pair;
        if (const Json * v = item.get("prompt")) pair.prompt = v->as_str("");
        if (const Json * v = item.get("answer")) pair.answer = v->as_str("");
        if (pair.prompt.empty()) {
            continue;
        }
        out.push_back(pair);
    }
}

} // namespace

ApiServer::ApiServer(Engine & engine, const ServerOptions & opts) : engine_(engine), opts_(opts) {}

void ApiServer::register_routes(HttpServer & server) {
    server.route("GET", "/health", [this](const HttpRequest & req, HttpResponse & resp) { handle_health(req, resp); });
    server.route("GET", "/props", [this](const HttpRequest & req, HttpResponse & resp) { handle_props(req, resp); });
    server.route("GET", "/slots", [this](const HttpRequest & req, HttpResponse & resp) { handle_slots(req, resp); });
    server.route("GET", "/metrics", [this](const HttpRequest & req, HttpResponse & resp) { handle_metrics(req, resp); });
    server.route("GET", "/v1/models", [this](const HttpRequest & req, HttpResponse & resp) { handle_models(req, resp); });
    server.route("GET", "/", [this](const HttpRequest & req, HttpResponse & resp) { handle_index(req, resp); });
    server.route("POST", "/tokenize", [this](const HttpRequest & req, HttpResponse & resp) { handle_tokenize(req, resp); });
    server.route("POST", "/extras/tokenize/count", [this](const HttpRequest & req, HttpResponse & resp) { handle_tokenize_count(req, resp); });
    server.route("POST", "/detokenize", [this](const HttpRequest & req, HttpResponse & resp) { handle_detokenize(req, resp); });
    server.route("POST", "/apply-template", [this](const HttpRequest & req, HttpResponse & resp) { handle_apply_template(req, resp); });
    server.route("POST", "/v1/control-vector", [this](const HttpRequest & req, HttpResponse & resp) { handle_control_vector(req, resp); });
    server.route("POST", "/v1/voice", [this](const HttpRequest & req, HttpResponse & resp) { handle_voice(req, resp); });
    server.route("POST", "/v1/voice/verify", [this](const HttpRequest & req, HttpResponse & resp) { handle_voice_verify(req, resp); });
    server.route("POST", "/v1/lora-adapters", [this](const HttpRequest & req, HttpResponse & resp) { handle_lora_adapters(req, resp); });
    server.route("POST", "/v1/model", [this](const HttpRequest & req, HttpResponse & resp) { handle_model(req, resp); });
    server.route("POST", "/completion", [this](const HttpRequest & req, HttpResponse & resp) { handle_completion(req, resp); });
    server.route_stream("POST", "/v1/chat/completions",
        [this](const HttpRequest & req, HttpResponse & resp, const std::function<bool()> & begin_stream,
               const std::function<bool(const std::string &)> & send) {
            handle_chat_completions(req, resp, begin_stream, send);
        });
    server.route_stream("POST", "/v1/completions",
        [this](const HttpRequest & req, HttpResponse & resp, const std::function<bool()> & begin_stream,
               const std::function<bool(const std::string &)> & send) {
            handle_completions(req, resp, begin_stream, send);
        });
}

void ApiServer::handle_health(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("model", Json::make_string(opts_.model_name));
    resp.body = out.dump();
}

void ApiServer::handle_props(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    const Engine::Config & cfg = engine_.config();
    Json out = Json::make_object();
    out.set("model_path", Json::make_string(cfg.model_path));
    out.set("model_desc", Json::make_string(engine_.model_desc()));
    out.set("chat_template", Json::make_string(engine_.chat_template_raw()));
    out.set("chat_template_mode", Json::make_string(engine_.template_mode()));
    out.set("n_ctx", Json::make_int(cfg.n_ctx));
    out.set("n_ctx_seq", Json::make_int(engine_.n_ctx_seq()));
    out.set("n_ctx_train", Json::make_int(engine_.n_ctx_train()));
    out.set("n_vocab", Json::make_int(engine_.n_vocab()));
    out.set("n_embd", Json::make_int(engine_.n_embd()));
    out.set("n_layers", Json::make_int(engine_.n_layers()));
    out.set("n_gpu_layers", Json::make_int(cfg.n_gpu_layers));
    out.set("n_parallel", Json::make_int(cfg.n_parallel));
    out.set("cache_type_k", Json::make_string(cfg.cache_type_k));
    out.set("cache_type_v", Json::make_string(cfg.cache_type_v));
    out.set("flash_attn", Json::make_bool(cfg.flash_attn));
    out.set("build_info", Json::make_string("world-work-cpp (llama.cpp b11753)"));
    out.set("lora_path", Json::make_string(engine_.lora_path()));

    // Capability flag: the client sends a control vector with the request
    // when the speaking character has one. Other inference servers do not
    // know this field, so the client probes for it first.
    Json cvec = Json::make_object();
    cvec.set("supported", Json::make_bool(true));
    cvec.set("per_request", Json::make_bool(true));
    out.set("control_vector", cvec);

    // The server can build a voice vector for the model it has loaded: the
    // clients use this to give every character its own voice.
    Json voice = Json::make_object();
    voice.set("supported", Json::make_bool(true));
    voice.set("per_character", Json::make_bool(true));
    out.set("voice_generate", voice);

    Json defaults = Json::make_object();
    defaults.set("temperature", Json::make_number(opts_.defaults.temperature));
    defaults.set("top_p", Json::make_number(opts_.defaults.top_p));
    defaults.set("top_k", Json::make_int(opts_.defaults.top_k));
    defaults.set("min_p", Json::make_number(opts_.defaults.min_p));
    defaults.set("repeat_penalty", Json::make_number(opts_.defaults.repeat_penalty));
    out.set("default_generation_settings", defaults);
    resp.body = out.dump();
}

// Prometheus style counters. The upstream server has the same endpoint;
// without it there is no way to see how much work the model did.
void ApiServer::handle_metrics(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    const Engine::Counters c = engine_.counters();
    int busy = 0;
    for (const Engine::SlotInfo & s : engine_.slots_info()) {
        if (s.busy) busy += 1;
    }
    std::string out;
    out += "# HELP wwc_requests_total Completed requests.\n";
    out += "# TYPE wwc_requests_total counter\n";
    out += "wwc_requests_total " + std::to_string(c.requests) + "\n";
    out += "# HELP wwc_errors_total Failed requests.\n";
    out += "# TYPE wwc_errors_total counter\n";
    out += "wwc_errors_total " + std::to_string(c.errors) + "\n";
    out += "# HELP wwc_aborted_total Requests stopped because the client left.\n";
    out += "# TYPE wwc_aborted_total counter\n";
    out += "wwc_aborted_total " + std::to_string(c.aborted) + "\n";
    out += "# HELP wwc_prompt_tokens_total Prompt tokens processed.\n";
    out += "# TYPE wwc_prompt_tokens_total counter\n";
    out += "wwc_prompt_tokens_total " + std::to_string(c.prompt_tokens) + "\n";
    out += "# HELP wwc_completion_tokens_total Completion tokens generated.\n";
    out += "# TYPE wwc_completion_tokens_total counter\n";
    out += "wwc_completion_tokens_total " + std::to_string(c.completion_tokens) + "\n";
    out += "# HELP wwc_prompt_ms_total Prompt evaluation time.\n";
    out += "# TYPE wwc_prompt_ms_total counter\n";
    out += "wwc_prompt_ms_total " + std::to_string(static_cast<long long>(c.t_prompt_ms)) + "\n";
    out += "# HELP wwc_gen_ms_total Generation time.\n";
    out += "# TYPE wwc_gen_ms_total counter\n";
    out += "wwc_gen_ms_total " + std::to_string(static_cast<long long>(c.t_gen_ms)) + "\n";
    out += "# HELP wwc_slots_busy Slots currently generating.\n";
    out += "# TYPE wwc_slots_busy gauge\n";
    out += "wwc_slots_busy " + std::to_string(busy) + "\n";
    resp.content_type = "text/plain; charset=utf-8";
    resp.body = out;
}

void ApiServer::handle_slots(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    Json arr = Json::make_array();
    for (const Engine::SlotInfo & s : engine_.slots_info()) {
        Json j = Json::make_object();
        j.set("id", Json::make_int(s.id));
        j.set("seq_id", Json::make_int(s.seq_id));
        j.set("busy", Json::make_bool(s.busy));
        j.set("n_cached", Json::make_int(s.n_cached));
        j.set("n_prompt", Json::make_int(s.n_prompt));
        j.set("n_decoded", Json::make_int(s.n_decoded));
        j.set("n_reused", Json::make_int(s.n_reused));
        j.set("t_prompt_ms", Json::make_number(s.t_prompt_ms));
        j.set("t_gen_ms", Json::make_number(s.t_gen_ms));
        arr.push(j);
    }
    resp.body = arr.dump();
}

void ApiServer::handle_models(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    Json data = Json::make_array();
    Json item = Json::make_object();
    item.set("id", Json::make_string(opts_.model_name));
    item.set("object", Json::make_string("model"));
    item.set("created", Json::make_int(now_epoch()));
    item.set("owned_by", Json::make_string("world-work-cpp"));
    data.push(item);
    Json out = Json::make_object();
    out.set("object", Json::make_string("list"));
    out.set("data", data);
    resp.body = out.dump();
}

void ApiServer::handle_index(const HttpRequest & req, HttpResponse & resp) {
    (void) req;
    resp.content_type = "text/html; charset=utf-8";
    std::ostringstream os;
    os << "<!doctype html><meta charset=utf-8><title>world-work-cpp</title>"
       << "<h1>world-work-cpp</h1>"
       << "<p>OpenAI compatible local inference server.</p>"
       << "<ul>"
       << "<li>POST /v1/chat/completions</li>"
       << "<li>POST /v1/completions</li>"
       << "<li>POST /completion</li>"
       << "<li>POST /tokenize</li>"
       << "<li>POST /extras/tokenize/count</li>"
       << "<li>POST /detokenize</li>"
       << "<li>POST /apply-template</li>"
       << "<li>GET /health /props /slots /v1/models</li>"
       << "<li>POST /v1/control-vector</li>"
       << "</ul>";
    resp.body = os.str();
}

void ApiServer::handle_tokenize(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    std::string text;
    if (const Json * v = body.get("content")) {
        text = content_to_text(*v);
    } else if (const Json * v = body.get("prompt")) {
        text = content_to_text(*v);
    } else if (const Json * v = body.get("text")) {
        text = content_to_text(*v);
    }
    bool add_special = true;
    bool parse_special = false;
    if (const Json * v = body.get("add_special")) add_special = v->as_bool(true);
    if (const Json * v = body.get("parse_special")) parse_special = v->as_bool(false);

    std::vector<llama_token> tokens;
    if (!engine_.tokenize(text, tokens, add_special, parse_special, &err)) {
        resp.status = 400;
        resp.body = "{\"error\":\"tokenize failed\"}";
        return;
    }
    Json arr = Json::make_array();
    for (llama_token t : tokens) arr.push(Json::make_int(t));
    Json out = Json::make_object();
    out.set("tokens", arr);
    out.set("count", Json::make_int(static_cast<int64_t>(tokens.size())));
    resp.body = out.dump();
}

// llama_cpp.server compatibility: the backend tokenizer counts tokens
// through /extras/tokenize/count with a body of {"input": text}.
void ApiServer::handle_tokenize_count(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    std::string text;
    if (const Json * v = body.get("input")) {
        text = content_to_text(*v);
    } else if (const Json * v = body.get("content")) {
        text = content_to_text(*v);
    } else if (const Json * v = body.get("text")) {
        text = content_to_text(*v);
    }
    std::vector<llama_token> tokens;
    if (!engine_.tokenize(text, tokens, true, false, &err)) {
        resp.status = 400;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }
    Json out = Json::make_object();
    out.set("count", Json::make_int(static_cast<int64_t>(tokens.size())));
    resp.body = out.dump();
}

void ApiServer::handle_detokenize(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    const Json * arr = body.get("tokens");
    if (!body.is_object() || !arr || !arr->is_array()) {
        resp.status = 400;
        resp.body = "{\"error\":\"tokens array required\"}";
        return;
    }
    std::vector<llama_token> tokens;
    for (const Json & t : arr->items()) {
        tokens.push_back(static_cast<llama_token>(t.as_int(0)));
    }
    std::string text;
    engine_.detokenize(tokens, text);
    Json out = Json::make_object();
    out.set("content", Json::make_string(text));
    resp.body = out.dump();
}

void ApiServer::handle_apply_template(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    const std::vector<ChatMsg> msgs = parse_messages(body);
    bool add_gen = true;
    if (const Json * v = body.get("add_generation_prompt")) add_gen = v->as_bool(true);
    const bool thinking = request_enable_thinking(body, opts_.enable_thinking);
    Json out = Json::make_object();
    out.set("prompt", Json::make_string(engine_.render_prompt(msgs, add_gen, thinking)));
    out.set("chat_template_mode", Json::make_string(engine_.template_mode()));
    resp.body = out.dump();
}

// Hot-swaps the control vector of the running model. This makes persona
// experiments a single server start instead of one start per candidate.
void ApiServer::handle_control_vector(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    // The key has to be there. An empty path clears the vector: that is
    // how a client turns the voice layer off again.
    const Json * path_value = body.get("path");
    if (path_value == nullptr) {
        resp.status = 400;
        resp.body = "{\"error\":\"path required\"}";
        return;
    }
    const std::string path = path_value->as_str("");
    float scale = 1.0f;
    if (const Json * v = body.get("scale")) {
        scale = static_cast<float>(v->as_double(1.0));
    }
    int32_t layer_start = 1;
    if (const Json * v = body.get("layer_start")) {
        layer_start = static_cast<int32_t>(v->as_int(1));
    }
    int32_t layer_end = -1;
    if (const Json * v = body.get("layer_end")) {
        layer_end = static_cast<int32_t>(v->as_int(-1));
    }
    if (!engine_.set_control_vector(path, scale, layer_start, layer_end, &err)) {
        resp.status = 400;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }
    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("path", Json::make_string(path));
    out.set("scale", Json::make_number(scale));
    out.set("layer_start", Json::make_int(layer_start));
    out.set("layer_end", Json::make_int(layer_end));
    resp.body = out.dump();
}

// Checks a voice vector against the character's own lines.
//
// The numbers are deterministic: the negative log likelihood of the
// character's own lines with and without the vector, plus the same for plain
// assistant lines as a control. A judge model's opinion is not used here:
// the same prompt scored twice tells more than a subjective grade.
void ApiServer::handle_voice_verify(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    Engine::VoiceSpec spec;
    if (const Json * v = body.get("persona")) spec.persona = v->as_str("");
    if (const Json * v = body.get("neutral_persona")) spec.neutral_persona = v->as_str("");
    if (const Json * v = body.get("max_tokens")) spec.max_tokens = static_cast<int32_t>(v->as_int(96));
    if (const Json * v = body.get("capture_ctx")) spec.capture_ctx = static_cast<int32_t>(v->as_int(2048));
    if (const Json * v = body.get("repeat")) spec.repeat = static_cast<int32_t>(v->as_int(2));
    read_voice_pairs(body, "pairs", spec.positive);
    read_voice_pairs(body, "control_pairs", spec.negative);
    if (spec.positive.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"pairs required\"}";
        return;
    }
    std::string path;
    if (const Json * v = body.get("vector_path")) path = v->as_str("");
    float scale = 1.0f;
    if (const Json * v = body.get("scale")) scale = static_cast<float>(v->as_double(1.0));
    if (path.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"vector_path required\"}";
        return;
    }
    Engine::VoiceVerifyResult result;
    if (!engine_.verify_voice_vector(spec, path, scale, result, &err)) {
        resp.status = 500;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }
    auto score_json = [](const Engine::VoiceScore & s) {
        Json j = Json::make_object();
        j.set("nll", Json::make_number(s.nll));
        j.set("tokens", Json::make_int(s.tokens));
        j.set("samples", Json::make_int(s.samples));
        Json per = Json::make_array();
        for (double v : s.per_sample) {
            per.push(Json::make_number(v));
        }
        j.set("per_sample", per);
        return j;
    };
    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("verdict", Json::make_string(result.verdict));
    out.set("vector_path", Json::make_string(path));
    out.set("scale", Json::make_number(scale));
    out.set("character_delta", Json::make_number(result.character_delta));
    out.set("control_delta", Json::make_number(result.control_delta));
    Json character = Json::make_object();
    character.set("without", score_json(result.character_without));
    character.set("with", score_json(result.character_with));
    out.set("character", character);
    Json control = Json::make_object();
    control.set("without", score_json(result.control_without));
    control.set("with", score_json(result.control_with));
    out.set("control", control);
    Json notes = Json::make_array();
    for (const std::string & n : result.notes) {
        notes.push(Json::make_string(n));
    }
    out.set("notes", notes);
    resp.body = out.dump();
}

// Builds a voice vector for a character, for the model that is loaded now.
//
// The recipe: the same prompts are run twice, once with the character's own
// voice and once with a plain assistant. The residual stream is captured over
// the answer tokens, the two means are subtracted per layer, and the result is
// scaled to a fixed fraction of the residual norm. A voice vector is tied to
// the model it was measured on, so this endpoint has to run on the same
// server that serves the chat.
void ApiServer::handle_voice(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    Engine::VoiceSpec spec;
    if (const Json * v = body.get("name")) spec.name = v->as_str("");
    if (const Json * v = body.get("persona")) spec.persona = v->as_str("");
    if (const Json * v = body.get("neutral_persona")) spec.neutral_persona = v->as_str("");
    if (const Json * v = body.get("ratio")) spec.ratio = static_cast<float>(v->as_double(0.02));
    if (const Json * v = body.get("repeat")) spec.repeat = static_cast<int32_t>(v->as_int(2));
    if (const Json * v = body.get("max_tokens")) spec.max_tokens = static_cast<int32_t>(v->as_int(96));
    if (const Json * v = body.get("capture_ctx")) spec.capture_ctx = static_cast<int32_t>(v->as_int(2048));

    read_voice_pairs(body, "pairs", spec.positive);
    read_voice_pairs(body, "negative_pairs", spec.negative);
    if (const Json * v = body.get("paired")) spec.paired = v->as_bool(false);
    if (spec.positive.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"pairs required\"}";
        return;
    }

    Engine::VoiceVector vec;
    if (!engine_.generate_voice_vector(spec, vec, &err)) {
        resp.status = 500;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }

    std::string path;
    if (const Json * v = body.get("save_path")) path = v->as_str("");
    if (!path.empty()) {
        std::ofstream of(path, std::ios::binary);
        if (!of) {
            resp.status = 500;
            Json out = Json::make_object();
            out.set("error", Json::make_string("cannot write the vector file"));
            resp.body = out.dump();
            return;
        }
        of.write(reinterpret_cast<const char *>(vec.data.data()),
                 static_cast<std::streamsize>(vec.data.size() * sizeof(float)));
        of.close();
    }

    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("name", Json::make_string(spec.name));
    out.set("bytes", Json::make_int(static_cast<int64_t>(vec.data.size() * sizeof(float))));
    out.set("floats", Json::make_int(static_cast<int64_t>(vec.data.size())));
    out.set("layers", Json::make_int(engine_.n_layers() - 1));
    out.set("n_embd", Json::make_int(engine_.n_embd()));
    out.set("pairs", Json::make_int(vec.n_pairs));
    out.set("ratio", Json::make_number(vec.ratio));
    out.set("path", Json::make_string(path));
    Json norms = Json::make_array();
    for (double v : vec.residual_norms) {
        norms.push(Json::make_number(v));
    }
    out.set("residual_norms", norms);
    Json factors = Json::make_array();
    for (double v : vec.factors) {
        factors.push(Json::make_number(v));
    }
    out.set("factors", factors);
    resp.body = out.dump();
}

// Loads or unloads a LoRA adapter while the server runs. An empty path
// unloads the current one. The upstream server has the same endpoint; here
// it is what makes the fine-tune track usable without a restart.
void ApiServer::handle_lora_adapters(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    const Json * path_value = body.get("path");
    if (path_value == nullptr) {
        resp.status = 400;
        resp.body = "{\"error\":\"path required\"}";
        return;
    }
    const std::string path = path_value->as_str("");
    float scale = 1.0f;
    if (const Json * v = body.get("scale")) {
        scale = static_cast<float>(v->as_double(1.0));
    }
    if (!engine_.set_lora(path, scale, &err)) {
        resp.status = 400;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }
    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("path", Json::make_string(path));
    out.set("scale", Json::make_number(scale));
    resp.body = out.dump();
}

// Swaps the model file without restarting the process. The upstream server
// needs a restart for this.
void ApiServer::handle_model(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    const Json * path_value = body.get("path");
    if (path_value == nullptr) {
        resp.status = 400;
        resp.body = "{\"error\":\"path required\"}";
        return;
    }
    const std::string path = path_value->as_str("");
    if (!engine_.reload_model(path, &err)) {
        resp.status = 400;
        Json out = Json::make_object();
        out.set("error", Json::make_string(err));
        resp.body = out.dump();
        return;
    }
    Json out = Json::make_object();
    out.set("status", Json::make_string("ok"));
    out.set("model_desc", Json::make_string(engine_.model_desc()));
    out.set("n_embd", Json::make_int(engine_.n_embd()));
    out.set("n_layers", Json::make_int(engine_.n_layers()));
    out.set("n_ctx", Json::make_int(engine_.n_ctx_seq()));
    resp.body = out.dump();
}

void ApiServer::handle_chat_completions(const HttpRequest & req, HttpResponse & resp,
                                       const std::function<bool()> & begin_stream,
                                       const std::function<bool(const std::string &)> & send) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    const std::vector<ChatMsg> msgs = parse_messages(body);
    if (msgs.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"messages required\"}";
        return;
    }
    // A control vector that belongs to the request: the voice layer of the
    // character that is speaking. An empty path means "server default".
    std::string cv_path;
    float cv_scale = 1.0f;
    int32_t cv_layer_start = 1;
    int32_t cv_layer_end = -1;
    if (const Json * v = body.get("control_vector")) {
        if (v->is_object()) {
            if (const Json * p = v->get("path")) cv_path = p->as_str("");
            if (const Json * s = v->get("scale")) cv_scale = static_cast<float>(s->as_double(1.0));
            if (const Json * s = v->get("layer_start")) cv_layer_start = static_cast<int32_t>(s->as_int(1));
            if (const Json * s = v->get("layer_end")) cv_layer_end = static_cast<int32_t>(s->as_int(-1));
        }
    }
    // The vector is built for one model: n_embd * (n_layers - 1) float32.
    // A vector made for another model is skipped instead of failing the
    // turn: swapping the model must not turn every reply into an error.
    std::string cv_note;
    if (!cv_path.empty()) {
        std::ifstream probe(cv_path, std::ios::binary | std::ios::ate);
        const std::streamoff bytes = probe ? probe.tellg() : -1;
        const int64_t want = static_cast<int64_t>(engine_.n_embd()) * (engine_.n_layers() - 1) * 4;
        if (bytes < 0) {
            cv_note = "cannot open control vector file";
            cv_path.clear();
        } else if (static_cast<int64_t>(bytes) != want) {
            cv_note = "control vector is " + std::to_string(static_cast<long long>(bytes)) +
                      " bytes, this model needs " + std::to_string(static_cast<long long>(want));
            cv_path.clear();
            std::fprintf(stderr, "[cvec] skipped: %s\n", cv_note.c_str());
        }
    }
    // What voice layer actually ran: the request's own vector, the server
    // default, or nothing at all. A client that only sees "applied: false"
    // cannot tell "no voice" from "the default voice was in place", and those
    // two produce different text.
    auto cv_info_json = [&]() {
        const Engine::ControlVectorState st = engine_.control_vector_state();
        Json info = Json::make_object();
        info.set("applied", Json::make_bool(st.active));
        info.set("source", Json::make_string(
            st.active ? (st.is_default ? "default" : "request") : "none"));
        if (st.active) {
            info.set("path", Json::make_string(st.path));
            info.set("scale", Json::make_number(st.scale));
        }
        if (!cv_note.empty()) {
            info.set("note", Json::make_string(cv_note));
            info.set("request_skipped", Json::make_bool(true));
        }
        return info;
    };

    SamplingParams sp = opts_.defaults;
    apply_sampling(body, sp);
    const bool thinking = request_enable_thinking(body, opts_.enable_thinking);
    // the messages part stays in the KV cache, the generation prompt is
    // rendered again on every request - same rule as llama.cpp
    std::vector<size_t> user_offsets;
    const std::string messages_text = engine_.render_prompt(msgs, false, thinking, &user_offsets);
    PromptInput prompt;
    prompt.full = messages_text + engine_.generation_prompt(thinking);
    prompt.cacheable = messages_text;
    // a checkpoint at every user turn and at the border between the messages
    // and the generation prompt - the latter is the one that matters for the
    // next turn of a conversation
    prompt.checkpoint_offsets = user_offsets;
    prompt.checkpoint_offsets.push_back(messages_text.size());
    const bool stream = request_wants_stream(body);
    const std::string id = make_id("chatcmpl");
    const int64_t created = now_epoch();

    if (!stream) {
        GenResult res;
        std::string gerr;
        if (!engine_.generate_with_control_vector(prompt, sp, nullptr, res,
                                                      cv_path, cv_scale, cv_layer_start,
                                                      cv_layer_end, &gerr)) {
            resp.status = 500;
            Json out = Json::make_object();
            out.set("error", Json::make_string(gerr));
            resp.body = out.dump();
            return;
        }
        Json message = Json::make_object();
        message.set("role", Json::make_string("assistant"));
        message.set("content", Json::make_string(res.content));
        if (!res.reasoning.empty()) {
            message.set("reasoning_content", Json::make_string(res.reasoning));
        }
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("message", message);
        choice.set("finish_reason", Json::make_string(res.finish_reason));

        Json choices = Json::make_array();
        choices.push(choice);

        Json out = Json::make_object();
        out.set("id", Json::make_string(id));
        out.set("object", Json::make_string("chat.completion"));
        out.set("control_vector", cv_info_json());
        out.set("created", Json::make_int(created));
        out.set("model", Json::make_string(opts_.model_name));
        out.set("choices", choices);
        out.set("usage", usage_json(res));
        out.set("timings", timings_json(res));
        resp.body = out.dump();
        return;
    }

    begin_stream();

    auto chunk_header = [&](const char * object) {
        Json c = Json::make_object();
        c.set("id", Json::make_string(id));
        c.set("object", Json::make_string(object));
        c.set("created", Json::make_int(created));
        c.set("model", Json::make_string(opts_.model_name));
        return c;
    };
    auto sse = [&](const Json & payload) {
        send("data: " + payload.dump() + "\n\n");
    };

    {
        Json c = chunk_header("chat.completion.chunk");
        Json delta = Json::make_object();
        delta.set("role", Json::make_string("assistant"));
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("delta", delta);
        choice.set("finish_reason", Json::make_null());
        Json choices = Json::make_array();
        choices.push(choice);
        c.set("choices", choices);
        sse(c);
    }

    GenResult res;
    std::string gerr;
    TokenCallback cb = [&send](const std::string & text, bool is_reasoning) -> bool {
        Json c = Json::make_object();
        c.set("id", Json::make_string("chatcmpl-stream"));
        c.set("object", Json::make_string("chat.completion.chunk"));
        c.set("created", Json::make_int(now_epoch()));
        c.set("model", Json::make_string("world-work-cpp"));
        Json delta = Json::make_object();
        if (is_reasoning) {
            delta.set("reasoning_content", Json::make_string(text));
        } else {
            delta.set("content", Json::make_string(text));
        }
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("delta", delta);
        choice.set("finish_reason", Json::make_null());
        Json choices = Json::make_array();
        choices.push(choice);
        c.set("choices", choices);
        return send("data: " + c.dump() + "\n\n");
    };

    const bool ok = engine_.generate_with_control_vector(prompt, sp, cb, res,
                                                          cv_path, cv_scale, cv_layer_start,
                                                          cv_layer_end, &gerr);
    if (!ok) {
        Json c = Json::make_object();
        c.set("error", Json::make_string(gerr));
        send("data: " + c.dump() + "\n\n");
        send("data: [DONE]\n\n");
        return;
    }

    {
        Json c = chunk_header("chat.completion.chunk");
        Json delta = Json::make_object();
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("delta", delta);
        choice.set("finish_reason", Json::make_string(res.finish_reason));
        Json choices = Json::make_array();
        choices.push(choice);
        c.set("choices", choices);
        c.set("usage", usage_json(res));
        c.set("timings", timings_json(res));
        c.set("control_vector", cv_info_json());
        sse(c);
    }
    send("data: [DONE]\n\n");
}

void ApiServer::handle_completions(const HttpRequest & req, HttpResponse & resp,
                                   const std::function<bool()> & begin_stream,
                                   const std::function<bool(const std::string &)> & send) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    std::string prompt_text;
    if (const Json * v = body.get("prompt")) prompt_text = content_to_text(*v);
    if (prompt_text.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"prompt required\"}";
        return;
    }
    SamplingParams sp = opts_.defaults;
    apply_sampling(body, sp);
    PromptInput prompt;
    prompt.full = prompt_text;
    const bool stream = request_wants_stream(body);
    const std::string id = make_id("cmpl");

    if (!stream) {
        GenResult res;
        std::string gerr;
        if (!engine_.generate(prompt, sp, nullptr, res, &gerr)) {
            resp.status = 500;
            Json out = Json::make_object();
            out.set("error", Json::make_string(gerr));
            resp.body = out.dump();
            return;
        }
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("text", Json::make_string(res.content));
        choice.set("finish_reason", Json::make_string(res.finish_reason));
        Json choices = Json::make_array();
        choices.push(choice);
        Json out = Json::make_object();
        out.set("id", Json::make_string(id));
        out.set("object", Json::make_string("text_completion"));
        out.set("created", Json::make_int(now_epoch()));
        out.set("model", Json::make_string(opts_.model_name));
        out.set("choices", choices);
        out.set("usage", usage_json(res));
        out.set("timings", timings_json(res));
        resp.body = out.dump();
        return;
    }

    begin_stream();
    GenResult res;
    std::string gerr;
    TokenCallback cb = [&send](const std::string & text, bool is_reasoning) -> bool {
        if (is_reasoning) return true;
        Json c = Json::make_object();
        c.set("id", Json::make_string("cmpl-stream"));
        c.set("object", Json::make_string("text_completion"));
        c.set("created", Json::make_int(now_epoch()));
        c.set("model", Json::make_string("world-work-cpp"));
        Json choice = Json::make_object();
        choice.set("index", Json::make_int(0));
        choice.set("text", Json::make_string(text));
        choice.set("finish_reason", Json::make_null());
        Json choices = Json::make_array();
        choices.push(choice);
        c.set("choices", choices);
        return send("data: " + c.dump() + "\n\n");
    };
    const bool ok = engine_.generate(prompt, sp, cb, res, &gerr);
    if (!ok) {
        Json c = Json::make_object();
        c.set("error", Json::make_string(gerr));
        send("data: " + c.dump() + "\n\n");
        send("data: [DONE]\n\n");
        return;
    }
    Json c = Json::make_object();
    c.set("id", Json::make_string(id));
    c.set("object", Json::make_string("text_completion"));
    c.set("created", Json::make_int(now_epoch()));
    c.set("model", Json::make_string(opts_.model_name));
    Json choice = Json::make_object();
    choice.set("index", Json::make_int(0));
    choice.set("text", Json::make_string(""));
    choice.set("finish_reason", Json::make_string(res.finish_reason));
    Json choices = Json::make_array();
    choices.push(choice);
    c.set("choices", choices);
    c.set("usage", usage_json(res));
    send("data: " + c.dump() + "\n\n");
    send("data: [DONE]\n\n");
}

void ApiServer::handle_completion(const HttpRequest & req, HttpResponse & resp) {
    std::string err;
    Json body = Json::parse(req.body, &err);
    if (!body.is_object()) {
        resp.status = 400;
        resp.body = "{\"error\":\"invalid json body\"}";
        return;
    }
    std::string prompt_text;
    if (const Json * v = body.get("prompt")) prompt_text = content_to_text(*v);
    if (prompt_text.empty()) {
        resp.status = 400;
        resp.body = "{\"error\":\"prompt required\"}";
        return;
    }
    SamplingParams sp = opts_.defaults;
    apply_sampling(body, sp);
    if (const Json * v = body.get("n_predict")) sp.max_tokens = static_cast<int32_t>(v->as_int(sp.max_tokens));
    PromptInput prompt;
    prompt.full = prompt_text;

    GenResult res;
    std::string gerr;
    if (!engine_.generate(prompt, sp, nullptr, res, &gerr)) {
        resp.status = 500;
        Json out = Json::make_object();
        out.set("error", Json::make_string(gerr));
        resp.body = out.dump();
        return;
    }
    Json out = Json::make_object();
    out.set("content", Json::make_string(res.content));
    out.set("reasoning_content", Json::make_string(res.reasoning));
    out.set("stop", Json::make_bool(true));
    out.set("stopped_eos", Json::make_bool(res.finish_reason == "stop"));
    out.set("stopped_limit", Json::make_bool(res.finish_reason == "length"));
    out.set("stopped_word", Json::make_string(""));
    out.set("model", Json::make_string(opts_.model_name));
    out.set("prompt_tokens", Json::make_int(res.prompt_tokens));
    out.set("completion_tokens", Json::make_int(res.completion_tokens));
    out.set("timings", timings_json(res));
    resp.body = out.dump();
}

} // namespace wwc
