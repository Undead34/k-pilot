import inspect

import structlog
from pydantic_ai import Agent

from k_pilot.application.deps import AppDeps
from k_pilot.application.tools import media, notification, window
from k_pilot.infrastructure.observability import instrument_tool
from k_pilot.prompts import SYSTEM

logger = structlog.get_logger("k-pilot.agent")

# Simple por defecto, potente cuando se necesita
k_agent = Agent[AppDeps](
    name="k-pilot",
    model="deepseek:deepseek-chat",
    deps_type=AppDeps,
    instructions=SYSTEM,
)

logger.info("agent.configured", model="deepseek:deepseek-chat", tools=12)


@k_agent.instructions
def system_info(_):
    return inspect.cleandoc("""
        SYSTEM INFO
        ---------------------------------
        User: undead34@Undead34
        OS: Arch Linux x86_64
        Host: MS-7E06 (1.0)
        Kernel: Linux 6.19.9-arch1-1
        Uptime: 1 day, 6 hours, 10 mins
        Packages: 1741 (pacman), 11 (flatpak-system), 25 (flatpak-user)
        Shell: bash 5.3.9
        Display (VG27AQ3A): 2560x1440 in 27", 180 Hz [External]
        DE: KDE Plasma 6.6.3
        WM: KWin (Wayland)
        WM Theme: Klassy
        Terminal: rio 0.2.37
        CPU: 12th Gen Intel(R) Core(TM) i5-12400F (12) @ 5.60 GHz
        GPU: NVIDIA GeForce RTX 4060 [Discrete]
        Memory: 11.95 GiB / 15.40 GiB (78%)
        Swap: 4.38 GiB / 23.70 GiB (18%)
        Disk (/): 55.25 GiB / 103.66 GiB (53%) - ext4
        Disk (/home): 397.69 GiB / 465.76 GiB (85%) - btrfs
        Disk (/srv/storage): 525.48 GiB / 908.22 GiB (58%) - btrfs
        Local IP (wlan0): 192.168.0.213/24
        Locale: en_US.UTF-8
        ---------------------------------
    """)


# Notificaciones
k_agent.tool(instrument_tool("notify_user", notification.notify_user))

# Control de ventanas
k_agent.tool(instrument_tool("list_windows", window.list_windows))
k_agent.tool(instrument_tool("focus_window", window.focus_window))
k_agent.tool(instrument_tool("close_window", window.close_window))

# Control de media
k_agent.tool(instrument_tool("control_media", media.control_media))
k_agent.tool(instrument_tool("get_now_playing", media.get_now_playing))
k_agent.tool(instrument_tool("list_media_players", media.list_media_players))
k_agent.tool(instrument_tool("seek", media.seek))
k_agent.tool(instrument_tool("set_repeat", media.set_repeat))
k_agent.tool(instrument_tool("toggle_shuffle", media.toggle_shuffle))
k_agent.tool(instrument_tool("set_volume", media.set_volume))
