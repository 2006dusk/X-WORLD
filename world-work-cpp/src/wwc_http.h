// Minimal HTTP/1.1 server for the world-work-cpp inference endpoints.
//
// Scope is deliberately small: local loopback traffic only. It supports the
// verbs the OpenAI compatible routes need (GET/POST), keep-alive, and
// server-sent events for streaming completions.
#pragma once

#include <atomic>
#include <functional>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace wwc {

struct HttpRequest {
    std::string method;
    std::string path;
    std::string query;
    std::string body;
    std::map<std::string, std::string> headers;

    std::string header(const std::string & name) const;
};

struct HttpResponse {
    int status = 200;
    std::string content_type = "application/json; charset=utf-8";
    std::string body;
    std::vector<std::pair<std::string, std::string>> headers;
};

class HttpServer {
public:
    using Handler = std::function<void(const HttpRequest &, HttpResponse &)>;
    // Stream handlers may take over the connection: begin_stream() switches
    // the response to server-sent events, send() writes bytes to the client
    // immediately. A handler that never calls begin_stream() falls back to
    // the regular JSON response carried by HttpResponse.
    //
    // send() returns false when the client is gone. The handler is expected
    // to stop the generation in that case.
    using StreamHandler = std::function<void(const HttpRequest &,
                                             HttpResponse &,
                                             const std::function<bool()> & begin_stream,
                                             const std::function<bool(const std::string &)> & send)>;

    HttpServer(const std::string & host, int port);
    ~HttpServer();

    void route(const std::string & method, const std::string & path, Handler handler);
    void route_stream(const std::string & method, const std::string & path, StreamHandler handler);
    void run();
    void stop();

    int port() const { return port_; }
    // Open connections right now. Reported by the metrics endpoint.
    int active_connections() const { return active_.load(); }

private:
    struct Route {
        std::string method;
        std::string path;
        Handler handler;
        StreamHandler stream;
    };

    void handle_connection(long long client);
    void dispatch(const HttpRequest & req, HttpResponse & resp);

    std::string host_;
    int port_ = 0;
    std::vector<Route> routes_;
    std::atomic<bool> stop_{false};
    std::atomic<int> active_{0};
    long long listen_socket_ = -1;
};

} // namespace wwc
