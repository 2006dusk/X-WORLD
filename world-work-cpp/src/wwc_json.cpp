#include "wwc_json.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <sstream>

namespace wwc {

Json Json::make_null() { return Json(); }

Json Json::make_bool(bool v) {
    Json j;
    j.type_ = Type::Bool;
    j.bool_ = v;
    return j;
}

Json Json::make_number(double v) {
    Json j;
    j.type_ = Type::Number;
    j.num_ = v;
    return j;
}

Json Json::make_int(int64_t v) {
    Json j;
    j.type_ = Type::Number;
    j.num_ = static_cast<double>(v);
    return j;
}

Json Json::make_string(const std::string & v) {
    Json j;
    j.type_ = Type::String;
    j.str_ = v;
    return j;
}

Json Json::make_object() {
    Json j;
    j.type_ = Type::Object;
    return j;
}

Json Json::make_array() {
    Json j;
    j.type_ = Type::Array;
    return j;
}

bool Json::as_bool(bool def) const {
    if (type_ == Type::Bool) return bool_;
    if (type_ == Type::Number) return num_ != 0.0;
    return def;
}

double Json::as_double(double def) const {
    if (type_ == Type::Number) return num_;
    return def;
}

int64_t Json::as_int(int64_t def) const {
    if (type_ == Type::Number) return static_cast<int64_t>(num_);
    if (type_ == Type::Bool) return bool_ ? 1 : 0;
    return def;
}

std::string Json::as_str(const std::string & def) const {
    if (type_ == Type::String) return str_;
    return def;
}

bool Json::has(const std::string & key) const {
    return type_ == Type::Object && object_.find(key) != object_.end();
}

const Json * Json::get(const std::string & key) const {
    if (type_ != Type::Object) return nullptr;
    auto it = object_.find(key);
    return it == object_.end() ? nullptr : &it->second;
}

size_t Json::size() const {
    if (type_ == Type::Array) return array_.size();
    if (type_ == Type::Object) return object_.size();
    return 0;
}

void Json::set(const std::string & key, const Json & value) {
    if (type_ != Type::Object) {
        type_ = Type::Object;
    }
    object_[key] = value;
}

void Json::push(const Json & value) {
    if (type_ != Type::Array) {
        type_ = Type::Array;
    }
    array_.push_back(value);
}

namespace {

void escape_into(const std::string & s, std::string & out) {
    out.push_back('"');
    size_t i = 0;
    while (i < s.size()) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80) {
            switch (c) {
                case '"':  out += "\\\""; break;
                case '\\': out += "\\\\"; break;
                case '\b': out += "\\b"; break;
                case '\f': out += "\\f"; break;
                case '\n': out += "\\n"; break;
                case '\r': out += "\\r"; break;
                case '\t': out += "\\t"; break;
                default:
                    if (c < 0x20) {
                        char buf[8];
                        std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                        out += buf;
                    } else {
                        out.push_back(static_cast<char>(c));
                    }
            }
            ++i;
            continue;
        }
        // Multi byte sequence. The model can emit a byte token that does not
        // form a valid character (this shows up when a control vector pushes
        // the logits off distribution). Such bytes would make the JSON body
        // unparsable for the client, so invalid sequences are dropped.
        size_t len = 0;
        unsigned int cp = 0;
        if ((c & 0xE0) == 0xC0)      { len = 2; cp = c & 0x1Fu; }
        else if ((c & 0xF0) == 0xE0) { len = 3; cp = c & 0x0Fu; }
        else if ((c & 0xF8) == 0xF0) { len = 4; cp = c & 0x07u; }
        else { ++i; continue; }
        if (i + len > s.size()) {
            break;   // truncated tail
        }
        bool ok = true;
        for (size_t k = 1; k < len; ++k) {
            const unsigned char cc = static_cast<unsigned char>(s[i + k]);
            if ((cc & 0xC0) != 0x80) { ok = false; break; }
            cp = (cp << 6) | (cc & 0x3Fu);
        }
        if (!ok) { ++i; continue; }
        if ((len == 2 && cp < 0x80) ||
            (len == 3 && cp < 0x800) ||
            (len == 4 && (cp < 0x10000 || cp > 0x10FFFF)) ||
            (cp >= 0xD800 && cp <= 0xDFFF)) {
            ++i;
            continue;
        }
        out.append(s, i, len);
        i += len;
    }
    out.push_back('"');
}

void dump_into(const Json & j, std::string & out) {
    switch (j.type()) {
        case Json::Type::Null:   out += "null"; break;
        case Json::Type::Bool:   out += j.as_bool() ? "true" : "false"; break;
        case Json::Type::Number: {
            double v = j.as_double();
            if (std::isfinite(v) && v == std::floor(v) && std::fabs(v) < 9.0e15) {
                char buf[32];
                std::snprintf(buf, sizeof(buf), "%lld", static_cast<long long>(v));
                out += buf;
            } else if (!std::isfinite(v)) {
                out += "0";
            } else {
                char buf[40];
                std::snprintf(buf, sizeof(buf), "%.17g", v);
                out += buf;
            }
            break;
        }
        case Json::Type::String: escape_into(j.as_str(), out); break;
        case Json::Type::Array: {
            out.push_back('[');
            bool first = true;
            for (const Json & item : j.items()) {
                if (!first) out.push_back(',');
                first = false;
                dump_into(item, out);
            }
            out.push_back(']');
            break;
        }
        case Json::Type::Object: {
            out.push_back('{');
            bool first = true;
            for (const auto & kv : j.fields()) {
                if (!first) out.push_back(',');
                first = false;
                escape_into(kv.first, out);
                out.push_back(':');
                dump_into(kv.second, out);
            }
            out.push_back('}');
            break;
        }
    }
}

struct Parser {
    const std::string & s;
    size_t i = 0;
    std::string err;

    explicit Parser(const std::string & text) : s(text) {}

    void skip_ws() {
        while (i < s.size()) {
            char c = s[i];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') { ++i; continue; }
            break;
        }
    }

    bool fail(const char * msg) {
        if (err.empty()) {
            std::ostringstream os;
            os << msg << " at offset " << i;
            err = os.str();
        }
        return false;
    }

    bool parse_hex4(unsigned int & out) {
        if (i + 4 > s.size()) return fail("bad unicode escape");
        out = 0;
        for (int k = 0; k < 4; ++k) {
            char c = s[i + k];
            out <<= 4;
            if (c >= '0' && c <= '9') out |= static_cast<unsigned int>(c - '0');
            else if (c >= 'a' && c <= 'f') out |= static_cast<unsigned int>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') out |= static_cast<unsigned int>(c - 'A' + 10);
            else return fail("bad unicode escape");
        }
        i += 4;
        return true;
    }

    static void put_utf8(unsigned int cp, std::string & out) {
        if (cp < 0x80) {
            out.push_back(static_cast<char>(cp));
        } else if (cp < 0x800) {
            out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else if (cp < 0x10000) {
            out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        } else {
            out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
            out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
        }
    }

    bool parse_string(std::string & out) {
        if (i >= s.size() || s[i] != '"') return fail("expected string");
        ++i;
        out.clear();
        while (true) {
            if (i >= s.size()) return fail("unterminated string");
            char c = s[i++];
            if (c == '"') return true;
            if (c != '\\') { out.push_back(c); continue; }
            if (i >= s.size()) return fail("unterminated escape");
            char e = s[i++];
            switch (e) {
                case '"':  out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/':  out.push_back('/'); break;
                case 'b':  out.push_back('\b'); break;
                case 'f':  out.push_back('\f'); break;
                case 'n':  out.push_back('\n'); break;
                case 'r':  out.push_back('\r'); break;
                case 't':  out.push_back('\t'); break;
                case 'u': {
                    unsigned int cp = 0;
                    if (!parse_hex4(cp)) return false;
                    if (cp >= 0xD800 && cp <= 0xDBFF && i + 1 < s.size() && s[i] == '\\' && s[i + 1] == 'u') {
                        size_t save = i;
                        i += 2;
                        unsigned int lo = 0;
                        if (!parse_hex4(lo)) return false;
                        if (lo >= 0xDC00 && lo <= 0xDFFF) {
                            cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                        } else {
                            i = save;
                        }
                    }
                    put_utf8(cp, out);
                    break;
                }
                default:
                    return fail("bad escape");
            }
        }
    }

    bool parse_value(Json & out) {
        skip_ws();
        if (i >= s.size()) return fail("unexpected end of input");
        char c = s[i];
        if (c == '{') return parse_object(out);
        if (c == '[') return parse_array(out);
        if (c == '"') {
            std::string v;
            if (!parse_string(v)) return false;
            out = Json::make_string(v);
            return true;
        }
        if (s.compare(i, 4, "true") == 0) { i += 4; out = Json::make_bool(true); return true; }
        if (s.compare(i, 5, "false") == 0) { i += 5; out = Json::make_bool(false); return true; }
        if (s.compare(i, 4, "null") == 0) { i += 4; out = Json::make_null(); return true; }
        return parse_number(out);
    }

    bool parse_number(Json & out) {
        size_t start = i;
        if (i < s.size() && (s[i] == '-' || s[i] == '+')) ++i;
        bool digits = false;
        while (i < s.size() && s[i] >= '0' && s[i] <= '9') { ++i; digits = true; }
        if (i < s.size() && s[i] == '.') {
            ++i;
            while (i < s.size() && s[i] >= '0' && s[i] <= '9') { ++i; digits = true; }
        }
        if (digits && i < s.size() && (s[i] == 'e' || s[i] == 'E')) {
            ++i;
            if (i < s.size() && (s[i] == '-' || s[i] == '+')) ++i;
            while (i < s.size() && s[i] >= '0' && s[i] <= '9') ++i;
        }
        if (!digits) return fail("invalid number");
        out = Json::make_number(std::strtod(s.substr(start, i - start).c_str(), nullptr));
        return true;
    }

    bool parse_array(Json & out) {
        out = Json::make_array();
        ++i; // '['
        skip_ws();
        if (i < s.size() && s[i] == ']') { ++i; return true; }
        while (true) {
            Json item;
            if (!parse_value(item)) return false;
            out.push(item);
            skip_ws();
            if (i >= s.size()) return fail("unterminated array");
            char c = s[i++];
            if (c == ']') return true;
            if (c != ',') return fail("expected , or ] in array");
        }
    }

    bool parse_object(Json & out) {
        out = Json::make_object();
        ++i; // '{'
        skip_ws();
        if (i < s.size() && s[i] == '}') { ++i; return true; }
        while (true) {
            skip_ws();
            std::string key;
            if (!parse_string(key)) return false;
            skip_ws();
            if (i >= s.size() || s[i] != ':') return fail("expected :");
            ++i;
            Json value;
            if (!parse_value(value)) return false;
            out.set(key, value);
            skip_ws();
            if (i >= s.size()) return fail("unterminated object");
            char c = s[i++];
            if (c == '}') return true;
            if (c != ',') return fail("expected , or } in object");
        }
    }
};

} // namespace

Json Json::parse(const std::string & text, std::string * err) {
    Parser p(text);
    Json out;
    if (!p.parse_value(out)) {
        if (err) *err = p.err;
        return Json();
    }
    p.skip_ws();
    if (p.i != text.size()) {
        if (err) *err = "trailing characters after JSON value";
        return Json();
    }
    return out;
}

std::string Json::dump() const {
    std::string out;
    dump_into(*this, out);
    return out;
}

} // namespace wwc
