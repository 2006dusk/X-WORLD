// Chat prompt rendering.
//
// The GGUF chat template of the target model is a Jinja2 document. This server
// does not embed a Jinja2 engine; instead it implements the text-only branch
// of the Qwen3.5 template verbatim (system / user / assistant, no tools, no
// multimodal parts). The rendering is verified token-by-token against the
// reference Jinja2 rendering, see tools/verify_template.py.
#pragma once

#include <string>
#include <vector>

namespace wwc {

struct ChatMsg {
    std::string role;      // system | user | assistant | tool
    std::string content;
    std::string reasoning;  // assistant reasoning_content, optional
};

// Python str.strip() semantics, matching Jinja2's |trim filter.
std::string trim_ws(const std::string & s);

// True when the raw GGUF chat template is the Qwen3.5 family template.
bool looks_like_qwen35(const std::string & chat_template);

// Renders messages with the Qwen3.5 text-only rules.
// user_offsets, when given, receives the character offsets of the user turns.
std::string render_qwen35(const std::vector<ChatMsg> & msgs,
                           bool add_generation_prompt,
                           bool enable_thinking,
                           std::vector<size_t> * user_offsets = nullptr);

} // namespace wwc
