// Extract down_proj weight information for the MLP neurons of one layer.
//
// Route 1 of the "neuron flipping" recipes adds a scalar offset s to a single
// MLP neuron activation. That offset reaches the residual stream through the
// neuron own down_proj column, so reproducing the recipe on top of a
// control vector (a plain additive offset on the residual tensor) only needs
// that column:
//
//     residual perturbation = s * W_down[:, n]
//
// The weights are read straight from the GGUF file with the public gguf
// API, so no model instance and no GPU memory are needed. The blob is
// dequantized row by row with the ggml dequantizers.
//
// Usage:
//   dump_ffn_weights --model <gguf> --layer 13 --neurons 100,200 \
//                    --out-norms norms.f32 --out-cols cols.f32
//
// Output:
//   --out-norms  f32[n_ff]                 L2 norm of every neuron column
//   --out-cols   f32[n_sel * n_embd]       selected columns, row major

#include "ggml.h"
#include "gguf.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

extern "C" {
void dequantize_row_q2_K (const void * x, float * y, int64_t k);
void dequantize_row_q3_K (const void * x, float * y, int64_t k);
void dequantize_row_q4_0 (const void * x, float * y, int64_t k);
void dequantize_row_q4_1 (const void * x, float * y, int64_t k);
void dequantize_row_q4_K (const void * x, float * y, int64_t k);
void dequantize_row_q5_0 (const void * x, float * y, int64_t k);
void dequantize_row_q5_1 (const void * x, float * y, int64_t k);
void dequantize_row_q5_K (const void * x, float * y, int64_t k);
void dequantize_row_q6_K (const void * x, float * y, int64_t k);
void dequantize_row_q8_0 (const void * x, float * y, int64_t k);
void dequantize_row_q8_K (const void * x, float * y, int64_t k);
}

namespace {

bool dequantize_row(ggml_type type, const void * src, float * dst, int64_t k) {
    switch (type) {
        case GGML_TYPE_Q2_K:    dequantize_row_q2_K(src, dst, k); return true;
        case GGML_TYPE_Q3_K:    dequantize_row_q3_K(src, dst, k); return true;
        case GGML_TYPE_Q4_0:    dequantize_row_q4_0(src, dst, k); return true;
        case GGML_TYPE_Q4_1:    dequantize_row_q4_1(src, dst, k); return true;
        case GGML_TYPE_Q4_K:    dequantize_row_q4_K(src, dst, k); return true;
        case GGML_TYPE_Q5_0:    dequantize_row_q5_0(src, dst, k); return true;
        case GGML_TYPE_Q5_1:    dequantize_row_q5_1(src, dst, k); return true;
        case GGML_TYPE_Q5_K:    dequantize_row_q5_K(src, dst, k); return true;
        case GGML_TYPE_Q6_K:    dequantize_row_q6_K(src, dst, k); return true;
        case GGML_TYPE_Q8_0:    dequantize_row_q8_0(src, dst, k); return true;
        case GGML_TYPE_Q8_K:    dequantize_row_q8_K(src, dst, k); return true;
        case GGML_TYPE_F16: {
            const ggml_fp16_t * h = (const ggml_fp16_t *) src;
            for (int64_t i = 0; i < k; ++i) {
                dst[i] = ggml_fp16_to_fp32(h[i]);
            }
            return true;
        }
        case GGML_TYPE_F32: {
            memcpy(dst, src, (size_t) k * sizeof(float));
            return true;
        }
        default:
            return false;
    }
}

bool write_f32(const std::string & path, const std::vector<float> & data) {
    std::FILE * f = std::fopen(path.c_str(), "wb");
    if (!f) {
        std::fprintf(stderr, "cannot write %s\n", path.c_str());
        return false;
    }
    if (!data.empty()) {
        std::fwrite(data.data(), sizeof(float), data.size(), f);
    }
    std::fclose(f);
    return true;
}

} // namespace

int main(int argc, char ** argv) {
    std::string model_path;
    std::string norms_path;
    std::string cols_path;
    int layer = -1;
    std::vector<int64_t> neurons;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        auto need = [&](const char * name) -> std::string {
            if (i + 1 >= argc) {
                std::fprintf(stderr, "missing value for %s\n", name);
                std::exit(2);
            }
            return std::string(argv[++i]);
        };
        if (a == "--model") {
            model_path = need("--model");
        } else if (a == "--layer") {
            layer = std::atoi(need("--layer").c_str());
        } else if (a == "--neurons") {
            const std::string list = need("--neurons");
            size_t pos = 0;
            while (pos < list.size()) {
                size_t comma = list.find(',', pos);
                if (comma == std::string::npos) {
                    comma = list.size();
                }
                const std::string tok = list.substr(pos, comma - pos);
                if (!tok.empty()) {
                    neurons.push_back(std::atoll(tok.c_str()));
                }
                pos = comma + 1;
            }
        } else if (a == "--out-norms") {
            norms_path = need("--out-norms");
        } else if (a == "--out-cols") {
            cols_path = need("--out-cols");
        } else {
            std::fprintf(stderr, "unknown argument: %s\n", a.c_str());
            return 2;
        }
    }
    if (model_path.empty() || layer < 0) {
        std::fprintf(stderr, "--model and --layer are required\n");
        return 2;
    }

    gguf_init_params gparams;
    gparams.no_alloc = true;
    gparams.ctx = nullptr;
    gguf_context * gctx = gguf_init_from_file(model_path.c_str(), gparams);
    if (!gctx) {
        std::fprintf(stderr, "failed to open gguf: %s\n", model_path.c_str());
        return 1;
    }

    char name[128];
    std::snprintf(name, sizeof(name), "blk.%d.ffn_down.weight", layer);
    const int64_t tid = gguf_find_tensor(gctx, name);
    if (tid < 0) {
        std::fprintf(stderr, "tensor %s not found\n", name);
        gguf_free(gctx);
        return 1;
    }

    const ggml_type type = gguf_get_tensor_type(gctx, tid);
    const int64_t * ne = gguf_get_tensor_ne(gctx, tid);
    const int64_t n_ff   = ne[0];   // neurons along one row
    const int64_t n_embd = ne[1];   // rows == embedding dimensions
    const size_t  nbytes = gguf_get_tensor_size(gctx, tid);
    const size_t  data_off = gguf_get_data_offset(gctx) + gguf_get_tensor_offset(gctx, tid);

    const int64_t blck = ggml_blck_size(type);
    const size_t  tsz  = ggml_type_size(type);
    const size_t  row_bytes = (size_t) (n_ff / blck) * tsz;

    std::fprintf(stderr, "tensor %s type=%d ne=[%lld,%lld] bytes=%zu row_bytes=%zu\n",
                 name, (int) type, (long long) n_ff, (long long) n_embd, nbytes, row_bytes);

    std::FILE * f = std::fopen(model_path.c_str(), "rb");
    if (!f) {
        std::fprintf(stderr, "cannot reopen gguf\n");
        gguf_free(gctx);
        return 1;
    }
    std::vector<uint8_t> host(nbytes);
    // the model file is larger than 2 GB, so the 64 bit seek is required
    if (_fseeki64(f, (__int64) data_off, SEEK_SET) != 0 ||
        std::fread(host.data(), 1, nbytes, f) != nbytes) {
        std::fprintf(stderr, "failed to read tensor blob\n");
        std::fclose(f);
        gguf_free(gctx);
        return 1;
    }
    std::fclose(f);
    gguf_free(gctx);

    std::vector<float> row((size_t) n_ff);
    std::vector<double> sq((size_t) n_ff, 0.0);
    std::vector<std::vector<float>> cols;
    cols.reserve(neurons.size());
    for (size_t i = 0; i < neurons.size(); ++i) {
        cols.emplace_back((size_t) n_embd, 0.0f);
    }

    for (int64_t r = 0; r < n_embd; ++r) {
        const uint8_t * src = host.data() + (size_t) r * row_bytes;
        if (!dequantize_row(type, src, row.data(), n_ff)) {
            std::fprintf(stderr, "unsupported weight type %d\n", (int) type);
            return 1;
        }
        for (int64_t n = 0; n < n_ff; ++n) {
            sq[(size_t) n] += (double) row[(size_t) n] * (double) row[(size_t) n];
        }
        for (size_t i = 0; i < neurons.size(); ++i) {
            const int64_t n = neurons[i];
            if (n >= 0 && n < n_ff) {
                cols[i][(size_t) r] = row[(size_t) n];
            }
        }
    }

    std::vector<float> norms((size_t) n_ff);
    for (int64_t n = 0; n < n_ff; ++n) {
        norms[(size_t) n] = (float) std::sqrt(sq[(size_t) n]);
    }

    if (!norms_path.empty() && !write_f32(norms_path, norms)) {
        return 1;
    }
    if (!cols_path.empty()) {
        std::vector<float> flat;
        flat.reserve(cols.size() * (size_t) n_embd);
        for (const auto & c : cols) {
            flat.insert(flat.end(), c.begin(), c.end());
        }
        if (!write_f32(cols_path, flat)) {
            return 1;
        }
    }

    std::fprintf(stderr, "n_ff=%lld n_embd=%lld neurons=%zu\n",
                 (long long) n_ff, (long long) n_embd, neurons.size());
    for (size_t i = 0; i < neurons.size(); ++i) {
        const int64_t n = neurons[i];
        std::fprintf(stderr, "neuron %lld norm=%.6f\n", (long long) n, (double) norms[(size_t) n]);
    }
    return 0;
}
