<div align="center">
  <h1>K-Pilot 🤖</h1>
  <p><strong>AI Assistant for KDE Plasma 6 — Voice-controlled desktop automation</strong></p>
  <p>Built for Linux power users who want hands-free control over their Plasma desktop.</p>
  <br>

  [![License: GPL-3.0](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
  [![Python](https://img.shields.io/badge/Python-3.14+-3776AB?logo=python&logoColor=white)](https://python.org/)
  [![KDE Plasma](https://img.shields.io/badge/KDE%20Plasma-6-1D99F3?logo=kde&logoColor=white)](https://kde.org/plasma-desktop/)
  [![Wayland](https://img.shields.io/badge/Wayland-supported-FFBC00?logo=wayland&logoColor=white)](https://wayland.freedesktop.org/)

  [📘 **Architecture**](#architecture) | [🎙️ **Voice Setup**](#voice-setup) | [🔧 **Tools**](#tools)
</div>

---

## 🌟 What is K-Pilot?

**K-Pilot** is an AI-powered desktop assistant designed specifically for **KDE Plasma 6 on Wayland**.

It brings hands-free automation to your Linux workflow through:

- 🎙️ **Wake-word activation** — Just say *"Hey K-Pilot"*
- 🧠 **Dual-model architecture** — Gemini Live for conversation, DeepSeek for tool execution
- ⚡ **Real-time tool calling** — Control windows, media, notifications, and more
- 🔒 **Privacy-first** — Local wake-word detection, minimal data exposure

K-Pilot keeps your desktop domain clean while AI capabilities stay modular and replaceable.

## 🚀 Why choose K-Pilot

- 🎯 **Plasma-native** — Deep integration with KWin, MPRIS, and KDE notifications
- 🗣️ **Natural conversations** — Low-latency voice interaction with Gemini Live
- 🛠️ **Extensible tools** — Hexagonal architecture makes adding capabilities trivial
- 🖥️ **Wayland-ready** — Built for modern Linux, no X11 legacy baggage

## 🏗️ Architecture at a glance

K-Pilot follows **Hexagonal Architecture** (Ports & Adapters):

```
┌─────────────────────────────────────┐
│         Application Layer           │
│    (Agent Orchestration, Tools)     │
├─────────────────────────────────────┤
│           Domain Layer              │
│   (Models, Ports, Wayland abstractions) │
├─────────────────────────────────────┤
│       Infrastructure Layer          │
│  (Gemini Live, KWin, MPRIS, D-Bus)  │
└─────────────────────────────────────┘
```

- `src/k_pilot/domain`: Core business logic, models, and port definitions
- `src/k_pilot/application`: Agent coordination and tool implementations  
- `src/k_pilot/infrastructure`: External adapters (AI providers, KDE APIs, audio)

This keeps the domain clean and AI providers replaceable.

## ✨ Core capabilities

- ✅ **Window Management** — List, focus, move, resize via KWin D-Bus
- ✅ **Media Control** — MPRIS integration (play, pause, metadata)
- ✅ **Notifications** — Read and dismiss KDE notifications
- ✅ **Wake Word** — Local "Hey K-Pilot" detection with `local-wake`
- ✅ **Voice I/O** — Real-time audio streaming with Gemini Live

## 🎯 Quick start

### Prerequisites

- KDE Plasma 6 (Wayland session)
- Python 3.14+
- `uv` for dependency management
- Microphone access (PipeWire/PulseAudio)

### Setup

```bash
# Clone and enter directory
git clone <your-repo> && cd k-pilot

# Create venv with system site packages (for PyGObject/D-Bus)
deactivate 2>/dev/null || true
rm -rf .venv
uv python pin 3.14
python3.14 -m venv --system-site-packages .venv
source .venv/bin/activate
uv sync --python 3.14
```

### Configuration

Create a `.env` file:

```bash
GOOGLE_API_KEY=your_gemini_api_key
DEEPSEEK_API_KEY=your_deepseek_api_key
K_PILOT_LOG_LEVEL=INFO
```

### Run

```bash
# Start the assistant
k-pilot

# Or via module
python -m k_pilot
```

Say *"Hey K-Pilot"* followed by your command!

## 🔧 Tools

| Tool | Description | Example |
|------|-------------|---------|
| `window.list` | List all open windows | *"Show me my windows"* |
| `window.focus` | Focus window by title/app | *"Focus Firefox"* |
| `media.play_pause` | Toggle playback | *"Pause the music"* |
| `media.get_info` | Current track info | *"What's playing?"* |
| `notification.list` | Read notifications | *"Do I have any notifications?"* |
| `notification.dismiss` | Clear notifications | *"Dismiss all notifications"* |

## 📊 Observability

| Variable | Effect |
|----------|--------|
| `K_PILOT_LOG_LEVEL=DEBUG` | Verbose turn timing and tool traces |
| `K_PILOT_LOG_JSON=1` | Structured JSON logging |
| `K_PILOT_MOCK_AUDIO=1` | Test without microphone |

Every agent turn emits:
- `agent.turn.started` / `agent.turn.completed` — with `duration_ms`, `provider`, `model`
- `tool.started` / `tool.completed` / `tool.failed` — with `tool_name`, `tool_call_id`

> 🔒 Sensitive arguments (`command`, `text`, `summary`) are automatically redacted in logs.

## 🛠️ Developer workflow

```bash
# Type checking (basedpyright)
uv run basedpyright

# Run tests
uv run pytest

# Check dependencies
uv tree
```

## 📄 License

K-Pilot is free software licensed under the **GNU General Public License v3.0**.  
See [LICENSE](LICENSE) for full terms.

---

<div align="center">
  <sub>Built with ❤️ for the KDE community. Not affiliated with KDE e.V.</sub>
</div>
