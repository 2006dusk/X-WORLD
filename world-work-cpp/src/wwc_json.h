// Minimal JSON value used by the HTTP layer and the OpenAI compatible routes.
//
// Only what the server actually needs: parse, read, build, serialize.
// Objects keep their keys sorted so the emitted payloads are stable.
#pragma once

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace wwc {

class Json {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Json() = default;

    static Json make_null();
    static Json make_bool(bool v);
    static Json make_number(double v);
    static Json make_int(int64_t v);
    static Json make_string(const std::string & v);
    static Json make_object();
    static Json make_array();

    // Parses a JSON document. Returns a null value on failure and fills err.
    static Json parse(const std::string & text, std::string * err = nullptr);

    Type type() const { return type_; }
    bool is_null() const { return type_ == Type::Null; }
    bool is_bool() const { return type_ == Type::Bool; }
    bool is_number() const { return type_ == Type::Number; }
    bool is_string() const { return type_ == Type::String; }
    bool is_array() const { return type_ == Type::Array; }
    bool is_object() const { return type_ == Type::Object; }

    bool as_bool(bool def = false) const;
    double as_double(double def = 0.0) const;
    int64_t as_int(int64_t def = 0) const;
    std::string as_str(const std::string & def = std::string()) const;

    bool has(const std::string & key) const;
    const Json * get(const std::string & key) const;
    size_t size() const;
    const std::vector<Json> & items() const { return array_; }
    const std::map<std::string, Json> & fields() const { return object_; }

    void set(const std::string & key, const Json & value);
    void push(const Json & value);

    std::string dump() const;

private:
    Type type_ = Type::Null;
    bool bool_ = false;
    double num_ = 0.0;
    std::string str_;
    std::vector<Json> array_;
    std::map<std::string, Json> object_;
};

} // namespace wwc
