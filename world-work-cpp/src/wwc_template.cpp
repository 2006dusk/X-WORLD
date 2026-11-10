#include "wwc_template.h"

#include <cctype>

namespace wwc {

namespace {

// The ChatML markers of the Qwen3.5 template. They are spelled out here so
// that the rendering stays in one place.
const char * kImStart = "<|im_start|>";
const char * kImEnd   = "<|im_end|>";

bool is_space(char c) {
    return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f' || c == '\v';
}

std::string lstrip_newlines(const std::string & s) {
    size_t i = 0;
    while (i < s.size() && s[i] == '\n') ++i;
    return s.substr(i);
}

std::string rstrip_newlines(const std::string & s) {
    size_t n = s.size();
    while (n > 0 && s[n - 1] == '\n') --n;
    return s.substr(0, n);
}

} // namespace

std::string trim_ws(const std::string & s) {
    size_t b = 0;
    size_t e = s.size();
    while (b < e && is_space(s[b])) ++b;
    while (e > b && is_space(s[e - 1])) --e;
    return s.substr(b, e - b);
}

bool looks_like_qwen35(const std::string & chat_template) {
    return chat_template.find("namespace(multi_step_tool") != std::string::npos;
}

std::string render_qwen35(const std::vector<ChatMsg> & msgs,
                           bool add_generation_prompt,
                           bool enable_thinking,
                           std::vector<size_t> * user_offsets) {
    std::string out;
    if (msgs.empty()) return out;

    // The reference template walks the messages backwards and remembers the
    // index of the last user turn; assistant turns after it carry an explicit
    // <think> block.
    size_t last_query_index = 0;
    bool has_user = false;
    for (size_t i = 0; i < msgs.size(); ++i) {
        if (msgs[i].role == "user") {
            last_query_index = i;
            has_user = true;
        }
    }
    if (!has_user) {
        last_query_index = msgs.size() - 1;
    }

    if (msgs[0].role == "system") {
        out += kImStart;
        out += "system\n";
        out += trim_ws(msgs[0].content);
        out += kImEnd;
        out += "\n";
    }

    for (size_t i = 0; i < msgs.size(); ++i) {
        const ChatMsg & m = msgs[i];
        if (m.role == "system") continue;

        std::string content = trim_ws(m.content);
        if (m.role == "user") {
            if (user_offsets) {
                user_offsets->push_back(out.size());
            }
            out += kImStart;
            out += "user\n";
            out += content;
            out += kImEnd;
            out += "\n";
            continue;
        }
        if (m.role != "assistant") {
            // tool turns are not part of the text-only branch
            out += m.role;
            out += "\n";
            out += content;
            out += "\n";
            continue;
        }

        std::string reasoning = m.reasoning;
        if (reasoning.empty()) {
            size_t close_tag = content.find("</think>");
            if (close_tag != std::string::npos) {
                reasoning = content.substr(0, close_tag);
                reasoning = rstrip_newlines(reasoning);
                size_t open_tag = reasoning.rfind("<think>");
                if (open_tag != std::string::npos) {
                    reasoning = reasoning.substr(open_tag + 7);
                }
                reasoning = lstrip_newlines(reasoning);
                content = lstrip_newlines(content.substr(close_tag + 8));
            }
        }
        reasoning = trim_ws(reasoning);

        out += kImStart;
        if (i > last_query_index) {
            out += "assistant\n<think>\n";
            out += reasoning;
            out += "\n</think>\n\n";
            out += content;
        } else {
            out += "assistant\n";
            out += content;
        }
        out += kImEnd;
        out += "\n";
    }

    if (add_generation_prompt) {
        out += kImStart;
        out += "assistant\n";
        out += enable_thinking ? "<think>\n" : "<think>\n\n</think>\n\n";
    }
    return out;
}

} // namespace wwc
