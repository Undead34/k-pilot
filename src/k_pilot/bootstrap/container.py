from pydantic_ai import Agent

# Skills
from k_pilot.bootstrap.skills import MediaSkill, NotificationSkill, WindowSkill

# Internals
from k_pilot.core.application.app_deps import AppDeps
from k_pilot.core.application.skill_protocol import KPilotSkill


class AppContainer:
    SKILLS: list[type[KPilotSkill]] = [
        WindowSkill,
        NotificationSkill,
        MediaSkill,
    ]

    def __init__(self) -> None:
        """Inicializa el estado vacío del contenedor."""
        self._configured: bool = False
        self._active_skills: list[type[KPilotSkill]] = []
        self._deps: AppDeps | None = None
        self._agent: Agent[AppDeps] | None = None

    def configure(self) -> None:
        """Configura los puertos y adaptadores (solo se ejecuta una vez)."""
        if self._configured:
            return

        active_skills = [skill for skill in self.SKILLS if skill.is_available()]

        self._deps = AppDeps()

        for skill in active_skills:
            skill.setup_deps(self._deps)

        self._configured = True
        self._active_skills = active_skills

    @property
    def agent(self) -> Agent[AppDeps]:
        """Expone el agente de forma segura. Lo crea (Lazy-load) si no existe."""
        if not self._configured:
            raise RuntimeError("Debes llamar a container.configure() antes de usar el agente.")

        if self._agent is None:
            self._agent = self._create_agent()

        return self._agent

    @property
    def deps(self) -> AppDeps:
        """Expone las dependencias del sistema de forma segura."""
        if not self._configured or self._deps is None:
            raise RuntimeError(
                "Debes llamar a container.configure() antes de pedir las dependencias."
            )

        return self._deps

    def _create_agent(self) -> Agent[AppDeps]:
        """Factory method privado: agente completamente configurado."""
        import inspect

        from pydantic_ai import Agent

        from k_pilot.core.shared.prompts import SYSTEM

        # Instanciamos el agente con su cerebro y modelo
        agent = Agent[AppDeps](
            name="k-pilot",
            model="deepseek:deepseek-chat",
            deps_type=AppDeps,
            instructions=SYSTEM,
        )

        # Inyectamos el contexto dinámico del sistema
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

        # Registro de tools a las Skills activas
        for skill in self._active_skills:
            skill.register_tools(agent)

        return agent


# Singleton global
container = AppContainer()
