#include "wwc_http.h"

#include <atomic>
#include <cstring>
#include <sstream>
#include <thread>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
typedef SOCKET wwc_socket_t;
#define WWC_INVALID_SOCKET INVALID_SOCKET
#define WWC_CLOSE_SOCKET closesocket
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/socket.h>
#include <unistd.h>
typedef int wwc_socket_t;
#define WWC_INVALID_SOCKET (-1)
#define WWC_CLOSE_SOCKET ::close
#endif

namespace wwc {

namespace {

const int kMaxConcurrent = 64;
const int kRecvTimeoutSec = 120;

std::string lower(std::string s) {
    for (char & c : s) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c - 'A' + 'a');
    }
    return s;
}

bool send_all(wwc_socket_t s, const std::string & data) {
    size_t sent = 0;
    while (sent < data.size()) {
        int n = static_cast<int>(::send(s, data.data() + sent, static_cast<int>(data.size() - sent), 0));
        if (n <= 0) return false;
        sent += static_cast<size_t>(n);
    }
    return true;
}

// True when the peer closed the connection.
//
// A client that goes away usually leaves a half-open socket: the next write
// may still succeed for a while. Peeking with a zero timeout tells us
// earlier - readable plus zero bytes means the peer sent EOF. Same check as
// the upstream llama.cpp server does before it keeps generating.
bool peer_gone(wwc_socket_t s) {
    fd_set rd;
    FD_ZERO(&rd);
    FD_SET(s, &rd);
    struct timeval tv;
    tv.tv_sec = 0;
    tv.tv_usec = 0;
    const int ready = ::select(int(s) + 1, &rd, nullptr, nullptr, &tv);
    if (ready <= 0) {
        return false;
    }
    char c = 0;
    const int n = ::recv(s, &c, 1, MSG_PEEK);
    return n == 0;
}

bool recv_more(wwc_socket_t s, std::string & buf) {
    char tmp[16384];
    int n = static_cast<int>(::recv(s, tmp, sizeof(tmp), 0));
    if (n <= 0) return false;
    buf.append(tmp, static_cast<size_t>(n));
    return true;
}

std::string status_text(int code) {
    switch (code) {
        case 200: return "OK";
        case 400: return "Bad Request";
        case 404: return "Not Found";
        case 405: return "Method Not Allowed";
        case 413: return "Payload Too Large";
        case 500: return "Internal Server Error";
        default:  return "OK";
    }
}

// Reads until the header terminator is present. Returns false on EOF/error.
bool read_head(wwc_socket_t s, std::string & buf, std::string & head) {
    size_t sep = std::string::npos;
    while (true) {
        sep = buf.find("\r\n\r\n");
        if (sep != std::string::npos) {
            head = buf.substr(0, sep + 4);
            buf.erase(0, sep + 4);
            return true;
        }
        if (buf.size() > 1 << 20) return false;
        if (!recv_more(s, buf)) return false;
    }
}

bool parse_head(const std::string & head, HttpRequest & req) {
    size_t pos = 0;
    size_t eol = head.find("\r\n");
    if (eol == std::string::npos) return false;
    std::string line = head.substr(0, eol);
    pos = eol + 2;

    size_t sp1 = line.find(' ');
    if (sp1 == std::string::npos) return false;
    size_t sp2 = line.find(' ', sp1 + 1);
    if (sp2 == std::string::npos) return false;
    req.method = line.substr(0, sp1);
    std::string target = line.substr(sp1 + 1, sp2 - sp1 - 1);

    size_t q = target.find('?');
    if (q == std::string::npos) {
        req.path = target;
    } else {
        req.path = target.substr(0, q);
        req.query = target.substr(q + 1);
    }

    while (pos < head.size()) {
        eol = head.find("\r\n", pos);
        if (eol == std::string::npos || eol == pos) break;
        std::string h = head.substr(pos, eol - pos);
        size_t colon = h.find(':');
        if (colon != std::string::npos) {
            std::string key = lower(h.substr(0, colon));
            std::string value = h.substr(colon + 1);
            size_t b = value.find_first_not_of(" \t");
            size_t e = value.find_last_not_of(" \t");
            value = (b == std::string::npos) ? std::string() : value.substr(b, e - b + 1);
            req.headers[key] = value;
        }
        pos = eol + 2;
    }
    return true;
}

bool read_chunked_body(wwc_socket_t s, std::string & buf, std::string & body) {
    while (true) {
        size_t eol = buf.find("\r\n");
        while (eol == std::string::npos) {
            if (!recv_more(s, buf)) return false;
            eol = buf.find("\r\n");
        }
        std::string size_line = buf.substr(0, eol);
        buf.erase(0, eol + 2);
        size_t chunk_size = 0;
        try {
            chunk_size = static_cast<size_t>(std::stoul(size_line, nullptr, 16));
        } catch (...) {
            return false;
        }
        if (chunk_size == 0) {
            // consume trailer
            while (true) {
                size_t t = buf.find("\r\n");
                while (t == std::string::npos) {
                    if (!recv_more(s, buf)) return true;
                    t = buf.find("\r\n");
                }
                std::string trailer = buf.substr(0, t);
                buf.erase(0, t + 2);
                if (trailer.empty()) break;
            }
            return true;
        }
        while (buf.size() < chunk_size + 2) {
            if (!recv_more(s, buf)) return false;
        }
        body.append(buf, 0, chunk_size);
        buf.erase(0, chunk_size + 2);
    }
}

} // namespace

std::string HttpRequest::header(const std::string & name) const {
    auto it = headers.find(lower(name));
    return it == headers.end() ? std::string() : it->second;
}

HttpServer::HttpServer(const std::string & host, int port) : host_(host), port_(port) {
#ifdef _WIN32
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);
#endif
}

HttpServer::~HttpServer() {
    stop();
#ifdef _WIN32
    WSACleanup();
#endif
}

void HttpServer::route(const std::string & method, const std::string & path, Handler handler) {
    Route r;
    r.method = method;
    r.path = path;
    r.handler = std::move(handler);
    routes_.push_back(std::move(r));
}

void HttpServer::route_stream(const std::string & method, const std::string & path, StreamHandler handler) {
    Route r;
    r.method = method;
    r.path = path;
    r.stream = std::move(handler);
    routes_.push_back(std::move(r));
}

void HttpServer::dispatch(const HttpRequest & req, HttpResponse & resp) {
    bool path_matched = false;
    for (const Route & r : routes_) {
        if (r.path != req.path) continue;
        path_matched = true;
        if (r.method != req.method) continue;
        if (r.handler) r.handler(req, resp);
        return;
    }
    if (path_matched) {
        resp.status = 405;
        resp.body = "{\"error\":\"method not allowed\"}";
        return;
    }
    resp.status = 404;
    resp.body = "{\"error\":\"not found\"}";
}

void HttpServer::handle_connection(long long client) {
    wwc_socket_t s = static_cast<wwc_socket_t>(client);

#ifdef _WIN32
    DWORD tv = kRecvTimeoutSec * 1000;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char *>(&tv), sizeof(tv));
#else
    struct timeval tv;
    tv.tv_sec = kRecvTimeoutSec;
    tv.tv_usec = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
#endif

    std::string buf;
    while (!stop_.load()) {
        std::string head;
        if (!read_head(s, buf, head)) break;
        HttpRequest req;
        if (!parse_head(head, req)) break;

        const std::string te = lower(req.header("transfer-encoding"));
        if (te.find("chunked") != std::string::npos) {
            if (!read_chunked_body(s, buf, req.body)) break;
        } else {
            const std::string cl = req.header("content-length");
            size_t want = 0;
            if (!cl.empty()) {
                try {
                    want = static_cast<size_t>(std::stoull(cl));
                } catch (...) {
                    want = 0;
                }
            }
            if (want > (32u << 20)) break;
            while (buf.size() < want) {
                if (!recv_more(s, buf)) break;
            }
            if (buf.size() < want) break;
            req.body = buf.substr(0, want);
            buf.erase(0, want);
        }

        std::fprintf(stderr, "[http] %s %s len=%zu\n",
                     req.method.c_str(), req.path.c_str(), req.body.size());
        std::fflush(stderr);

        HttpResponse resp;

        // streaming route?
        const Route * stream_route = nullptr;
        for (const Route & r : routes_) {
            if (r.path == req.path && r.method == req.method && r.stream) {
                stream_route = &r;
                break;
            }
        }
        if (stream_route) {
            bool streaming = false;
            auto begin_stream = [&]() {
                if (streaming) return true;
                std::string head_out = "HTTP/1.1 200 OK\r\n";
                head_out += "Content-Type: text/event-stream\r\n";
                head_out += "Cache-Control: no-cache\r\n";
                head_out += "Connection: close\r\n";
                head_out += "\r\n";
                if (!send_all(s, head_out)) return false;
                streaming = true;
                return true;
            };
            // send() reports whether the client is still there: the handler
            // stops the generation when it is not.
            auto send = [s](const std::string & chunk) -> bool {
                if (peer_gone(s)) {
                    return false;
                }
                return send_all(s, chunk);
            };
            try {
                stream_route->stream(req, resp, begin_stream, send);
            } catch (...) {
                // the client went away mid stream; nothing to recover here
            }
            if (streaming) {
                break;
            }
            // the handler chose the plain response path
        }

        try {
            dispatch(req, resp);
        } catch (...) {
            resp.status = 500;
            resp.body = "{\"error\":\"internal error\"}";
        }

        std::string out = "HTTP/1.1 " + std::to_string(resp.status) + " " + status_text(resp.status) + "\r\n";
        out += "Content-Type: " + resp.content_type + "\r\n";
        out += "Content-Length: " + std::to_string(resp.body.size()) + "\r\n";
        for (const auto & h : resp.headers) {
            out += h.first + ": " + h.second + "\r\n";
        }
        const std::string conn = lower(req.header("connection"));
        const bool close_after = (conn == "close") || req.method == "HEAD";
        out += close_after ? "Connection: close\r\n" : "Connection: keep-alive\r\n";
        out += "\r\n";
        out += resp.body;
        if (!send_all(s, out)) break;
        if (close_after) break;
    }

    WWC_CLOSE_SOCKET(s);
    active_.fetch_sub(1);
}

void HttpServer::run() {
    addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;
    hints.ai_socktype = SOCK_STREAM;
    hints.ai_flags = AI_PASSIVE;

    addrinfo * res = nullptr;
    const std::string port_str = std::to_string(port_);
    if (::getaddrinfo(host_.empty() ? nullptr : host_.c_str(), port_str.c_str(), &hints, &res) != 0 || res == nullptr) {
        throw std::runtime_error("cannot resolve listen address");
    }

    wwc_socket_t ls = WWC_INVALID_SOCKET;
    for (addrinfo * p = res; p != nullptr; p = p->ai_next) {
        ls = ::socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (ls == WWC_INVALID_SOCKET) continue;
        int yes = 1;
        ::setsockopt(ls, SOL_SOCKET, SO_REUSEADDR, reinterpret_cast<const char *>(&yes), sizeof(yes));
        if (::bind(ls, p->ai_addr, static_cast<int>(p->ai_addrlen)) == 0) break;
        WWC_CLOSE_SOCKET(ls);
        ls = WWC_INVALID_SOCKET;
    }
    ::freeaddrinfo(res);
    if (ls == WWC_INVALID_SOCKET) {
        throw std::runtime_error("cannot bind listen socket");
    }
    if (::listen(ls, 64) != 0) {
        WWC_CLOSE_SOCKET(ls);
        throw std::runtime_error("cannot listen");
    }
    listen_socket_ = static_cast<long long>(ls);

    while (!stop_.load()) {
        wwc_socket_t client = ::accept(ls, nullptr, nullptr);
        if (client == WWC_INVALID_SOCKET) {
            if (stop_.load()) break;
            continue;
        }
        if (active_.load() >= kMaxConcurrent) {
            WWC_CLOSE_SOCKET(client);
            continue;
        }
        active_.fetch_add(1);
        std::thread th(&HttpServer::handle_connection, this, static_cast<long long>(client));
        th.detach();
    }

    if (ls != WWC_INVALID_SOCKET) {
        WWC_CLOSE_SOCKET(ls);
    }
    listen_socket_ = -1;
}

void HttpServer::stop() {
    stop_.store(true);
    if (listen_socket_ >= 0) {
        ::shutdown(static_cast<wwc_socket_t>(listen_socket_), 2);
    }
}

} // namespace wwc
