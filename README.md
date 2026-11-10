# X-WORLD

X-WORLD 是一个本地运行的 RP 客户端。角色卡、世界书、上下文装配、回合闸门、符合度判定与推理服务都在本机完成。

X-WORLD is a role-play client that runs locally. Character cards, worldbooks, context assembly, turn gates, conformance checks, and inference all run on one machine.

## 1. 能力 / Capabilities

- 角色卡：三层设定、状态字段、关系档、标签受控词表、卡片分区视图。
- 世界书：条目按关键词匹配注入，条目可绑角色与地区。
- 上下文装配：预算裁剪、检索窗口、世界状态、场景与动作分块。
- 回合闸门：自称校验、身份一致性、括号动作、台词归位、复读重采样。
- 符合度判定：结构、身份、声线三层；任一层不通过即触发重写或归一。
- 记忆：会话流水 JSONL、向量索引、抽取与去重、落盘索引。
- 分支：会话分叉树按角色与分支组织，删除只删分支。
- 推理：OpenAI 兼容端点与槽位调度、KV 前缀复用、按请求挂载控制向量与声线向量、原地换模型、LoRA 运行时挂载、`/metrics`。
- 声线：向量按角色生成、按角色挂载、剂量分级。
- 插件：清单式插件、子进程宿主、内置正则插件与世界书插件。
- 常驻运行：监督器保活后端；推理服务是独立进程，后端重启后按端口认领。
- 局域网访问：`XWORLD_WEB_HOST` 开启。

- Character cards: three-layer definition, state fields, relationship tiers, controlled tag vocabulary, split card views.
- Worldbooks: entries are injected by keyword match, and an entry binds to a character or a region.
- Context assembly: budget trimming, retrieval windows, world state, scene and action blocks.
- Turn gates: self-reference check, identity consistency, parenthetical action, dialogue alignment, repetition resampling.
- Conformance checks: structure, identity, and voice layers; a failing layer triggers a rewrite or a normalization pass.
- Memory: JSONL conversation stream, vector index, extraction and dedup, persisted index.
- Branches: the session fork tree is organized by character and branch, and deletion removes a branch.
- Inference: OpenAI-compatible endpoint and slot scheduling, KV prefix reuse, per-request control and voice vectors, in-place model swap, runtime LoRA mounting, `/metrics`.
- Voice: vectors are generated per character, mounted per character, and dosed by tier.
- Plugins: manifest-based plugins, a subprocess host, built-in regex and worldbook plugins.
- Resident run: a supervisor keeps the backend alive; the inference server runs as its own process and is reclaimed by port after a backend restart.
- LAN access: enabled with `XWORLD_WEB_HOST`.

## 2. 组件 / Components

| 组件 | 形态 | 职责 |
|---|---|---|
| [backend/](backend/) | Python 3.12，FastAPI | 角色卡、世界书、上下文装配、回合闸门、符合度判定、记忆、插件、HTTP 接口 |
| [frontend/](frontend/) | Vue 3，TypeScript，Vite | 对话页、卡片编辑器、世界书、记忆、插件、设置页 |
| [world-work-cpp/](world-work-cpp/) | C++17，`world-work-cpp`（链接 llama.cpp 预编译 DLL） | OpenAI 兼容端点与槽位调度、KV 前缀复用、按请求挂载控制向量与声线向量、原地换模型、LoRA 运行时挂载、`/metrics` |

| Component | Stack | Responsibility |
|---|---|---|
| [backend/](backend/) | Python 3.12, FastAPI | Character cards, worldbooks, context assembly, turn gates, conformance checks, memory, plugins, HTTP API |
| [frontend/](frontend/) | Vue 3, TypeScript, Vite | Chat view, card editor, worldbooks, memory, plugins, settings |
| [world-work-cpp/](world-work-cpp/) | C++17, `world-work-cpp` (links prebuilt llama.cpp DLLs) | OpenAI-compatible endpoint and slot scheduling, KV prefix reuse, per-request control and voice vectors, in-place model swap, runtime LoRA mounting, `/metrics` |

## 3. 运行 / Running

前置：Python 3.12、Node.js、MSVC Build Tools 2022、CUDA 12.5、llama.cpp 预编译 DLL、GGUF 权重文件。

机器路径写在仓库根的 `local.paths.json`，该文件不入库，格式见 [local.paths.example.json](local.paths.example.json)。键：`llamacpp_dir`、`vcvars`、`cuda_bin`、`models_local_dir`、`models_hf_dir`、`models_ms_dir`。

编译推理服务端：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File world-work-cpp\build.ps1
```

编译分析工具：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File world-work-cpp\build_tools.ps1
```

启动后端：

```powershell
python backend\packaging\serve.py start
```

子命令：`status`、`stop`、`stop --with-inference`、`restart`。

前端开发服务器：

```bash
cd frontend
npm install
npm run dev
```

开发服务器把 `/v1` 代理到 `VITE_XW_BACKEND`，默认 `http://127.0.0.1:8760`；正式运行时后端托管 `frontend/dist`。

| 变量 | 作用 |
|---|---|
| `XWORLD_DATA_DIR` | 数据根目录，默认 `%APPDATA%\X-WORLD` |
| `XWORLD_WEB_HOST` | 网页服务监听地址，默认 `127.0.0.1` |
| `XWORLD_INFERENCE_TIMEOUT` | 推理调用超时秒数，默认 `600` |
| `XWORLD_INFERENCE_CONNECT_TIMEOUT` | 推理连接超时秒数，默认 `10` |
| `XWORLD_LOCAL_PATHS` | `local.paths.json` 的位置 |
| `XWORLD_LLAMACPP_DIR`、`XWORLD_VCVARS`、`XWORLD_CUDA_BIN` | 编译依赖根路径 |
| `XWORLD_MODEL` | 探针脚本默认权重文件 |
| `XWORLD_WWC_PATH` | 推理服务端可执行文件位置 |
| `XWORLD_SAMPLING_TEMPERATURE`、`XWORLD_SAMPLING_TOP_P`、`XWORLD_SAMPLING_EXTRA` | 采样参数，默认 `1.0`、`0.95`、`min_p=0.05` |

Prerequisites: Python 3.12, Node.js, MSVC Build Tools 2022, CUDA 12.5, prebuilt llama.cpp DLLs, a GGUF weight file.

Machine paths live in `local.paths.json` at the repository root. The file is not tracked, and its format is in [local.paths.example.json](local.paths.example.json). Keys: `llamacpp_dir`, `vcvars`, `cuda_bin`, `models_local_dir`, `models_hf_dir`, `models_ms_dir`.

Build the inference server:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File world-work-cpp\build.ps1
```

Build the analysis tools:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File world-work-cpp\build_tools.ps1
```

Start the backend:

```powershell
python backend\packaging\serve.py start
```

Subcommands: `status`, `stop`, `stop --with-inference`, `restart`.

Frontend development server:

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/v1` to `VITE_XW_BACKEND`, default `http://127.0.0.1:8760`. A production run serves `frontend/dist` from the backend.

| Variable | Effect |
|---|---|
| `XWORLD_DATA_DIR` | Data root directory, default `%APPDATA%\X-WORLD` |
| `XWORLD_WEB_HOST` | Web service bind address, default `127.0.0.1` |
| `XWORLD_INFERENCE_TIMEOUT` | Inference call timeout in seconds, default `600` |
| `XWORLD_INFERENCE_CONNECT_TIMEOUT` | Inference connect timeout in seconds, default `10` |
| `XWORLD_LOCAL_PATHS` | Location of `local.paths.json` |
| `XWORLD_LLAMACPP_DIR`, `XWORLD_VCVARS`, `XWORLD_CUDA_BIN` | Build dependency roots |
| `XWORLD_MODEL` | Default weight file for probe scripts |
| `XWORLD_WWC_PATH` | Location of the inference server executable |
| `XWORLD_SAMPLING_TEMPERATURE`, `XWORLD_SAMPLING_TOP_P`, `XWORLD_SAMPLING_EXTRA` | Sampling parameters, defaults `1.0`, `0.95`, `min_p=0.05` |

## 4. 数据与边界 / Data and Boundaries

- 数据根目录：`%APPDATA%\X-WORLD`，`XWORLD_DATA_DIR` 可覆盖。
- 数据层：JSON 是唯一权威格式；会话流水 JSONL；Markdown 旁挂。
- 网页服务默认端口 `8760`，监听 `127.0.0.1`。
- 推理服务端是独立进程，端口只绑本机。
- 仓库含后端、前端与推理服务端的源码和构建脚本；角色卡、世界书正文与模型权重在仓库之外。

- Data root directory: `%APPDATA%\X-WORLD`, overridable with `XWORLD_DATA_DIR`.
- Data layer: JSON is the only authoritative format, the conversation stream is JSONL, and Markdown sits alongside it.
- The web service listens on port `8760` and binds `127.0.0.1`.
- The inference server is a separate process and binds local ports only.
- The repository holds the backend, frontend, and inference server sources and build scripts. Character cards, worldbook text, and model weights live outside the repository.

## 5. 许可证 / License

PolyForm Noncommercial 1.0.0。许可只覆盖非商用用途，商用需另行授权。完整文本见 [LICENSE](LICENSE)，Required Notice 与授权入口见 [NOTICE.txt](NOTICE.txt)。

PolyForm Noncommercial 1.0.0. The license covers noncommercial use only; commercial use requires a separate license. The full text is in [LICENSE](LICENSE), and the Required Notice with the licensing contact is in [NOTICE.txt](NOTICE.txt).
