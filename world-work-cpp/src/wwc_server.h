// OpenAI compatible routes plus the llama.cpp specific introspection endpoints.
#pragma once

#include <string>
#include <vector>

#include "wwc_engine.h"
#include "wwc_http.h"
#include "wwc_json.h"

namespace wwc {

struct ServerOptions {
    std::string model_name = "world-work-cpp";
    bool enable_thinking = false;
    SamplingParams defaults;
};

class ApiServer {
public:
    ApiServer(Engine & engine, const ServerOptions & opts);

    void register_routes(HttpServer & server);

private:
    void handle_health(const HttpRequest & req, HttpResponse & resp);
    void handle_props(const HttpRequest & req, HttpResponse & resp);
    void handle_slots(const HttpRequest & req, HttpResponse & resp);
    void handle_metrics(const HttpRequest & req, HttpResponse & resp);
    void handle_models(const HttpRequest & req, HttpResponse & resp);
    void handle_index(const HttpRequest & req, HttpResponse & resp);
    void handle_tokenize(const HttpRequest & req, HttpResponse & resp);
    void handle_tokenize_count(const HttpRequest & req, HttpResponse & resp);
    void handle_detokenize(const HttpRequest & req, HttpResponse & resp);
    void handle_apply_template(const HttpRequest & req, HttpResponse & resp);
    void handle_control_vector(const HttpRequest & req, HttpResponse & resp);
    // Builds a voice vector for a character at runtime, for the model that
    // is loaded right now.
    void handle_voice(const HttpRequest & req, HttpResponse & resp);
    // Checks a voice vector against the character's own lines.
    void handle_voice_verify(const HttpRequest & req, HttpResponse & resp);
    void handle_lora_adapters(const HttpRequest & req, HttpResponse & resp);
    void handle_model(const HttpRequest & req, HttpResponse & resp);
    void handle_chat_completions(const HttpRequest & req, HttpResponse & resp,
                                 const std::function<bool()> & begin_stream,
                                 const std::function<bool(const std::string &)> & send);
    void handle_completions(const HttpRequest & req, HttpResponse & resp,
                            const std::function<bool()> & begin_stream,
                            const std::function<bool(const std::string &)> & send);
    void handle_completion(const HttpRequest & req, HttpResponse & resp);

    Engine & engine_;
    ServerOptions opts_;
};

} // namespace wwc
