import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from k_pilot.core.application.ports.driven import (
    MediaControlPort,
    NotificationPort,
    WindowManagerPort,
)

if TYPE_CHECKING:
    from pydantic_ai import Agent


@dataclass(frozen=True)
class AppDeps:
    """Inyección de dependencias para el agente."""

    notification_port: NotificationPort
    window_port: WindowManagerPort
    media_port: MediaControlPort


@dataclass
class AppContainer:
    """Contenedor de dependencias. No crea nada hasta que se llama."""

    _deps: AppDeps | None = field(default=None, repr=False)
    _agent: "Agent[AppDeps] | None" = field(default=None, repr=False)
    _configured: bool = field(default=False, repr=False)

    def configure(self) -> None:
        """Idempotente: solo corre una vez."""
        if self._configured:
            return

        from k_pilot.adapters.driven.kwin import KWinWindowAdapter
        from k_pilot.adapters.driven.mpris import MprisMediaAdapter
        from k_pilot.adapters.driven.notifications import FreedesktopNotificationAdapter

        self._deps = AppDeps(
            notification_port=FreedesktopNotificationAdapter(),
            window_port=KWinWindowAdapter(),
            media_port=MprisMediaAdapter(),
        )
        self._configured = True

    @property
    def deps(self) -> AppDeps:
        if self._deps is None:
            raise RuntimeError("Container not configured. Call configure() first.")
        return self._deps

    @property
    def agent(self) -> "Agent[AppDeps]":
        """Lazy factory: crea el agente solo cuando se necesita."""
        if self._agent is None:
            self._agent = self._create_agent()
        return self._agent

    def _create_agent(self) -> "Agent[AppDeps]":
        """Factory method: agente completamente configurado."""
        from pydantic_ai import Agent

        from k_pilot.adapters.driving.agent_tools import media, notification, window
        from k_pilot.core.shared.prompts.brain import SYSTEM

        agent = Agent[AppDeps](
            name="k-pilot",
            model="deepseek:deepseek-chat",
            deps_type=AppDeps,
            instructions=SYSTEM,
        )

        @agent.instructions
        def system_info(_):  # pyright: ignore[reportUnusedFunction]
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

        # Registrar tools
        agent.tool(notification.notify_user)
        agent.tool(window.list_windows)
        agent.tool(window.focus_window)
        agent.tool(window.close_window)
        agent.tool(media.control_media)
        agent.tool(media.get_now_playing)
        agent.tool(media.list_media_players)
        agent.tool(media.seek)
        agent.tool(media.set_repeat)
        agent.tool(media.toggle_shuffle)
        agent.tool(media.set_volume)

        return agent


# Singleton global - pero NO configura automáticamente
container = AppContainer()
