from pathlib import Path

from platformdirs import PlatformDirs


class AppPaths:
    """
    Gestor centralizado de rutas del sistema impulsado por platformdirs.
    Garantiza el cumplimiento estricto del estándar XDG Base Directory en Linux.
    """

    def __init__(self, app_name: str = "k-pilot", app_author: str = "undead34") -> None:
        # Inicializamos el gestor. En Linux el app_author suele ignorarse,
        # pero es buena práctica declararlo.
        self._dirs = PlatformDirs(appname=app_name, appauthor=app_author)

    @property
    def config_dir(self) -> Path:
        """~/.config/k-pilot"""
        return self._dirs.user_config_path

    @property
    def data_dir(self) -> Path:
        """~/.local/share/k-pilot"""
        return self._dirs.user_data_path

    @property
    def cache_dir(self) -> Path:
        """~/.cache/k-pilot"""
        return self._dirs.user_cache_path

    # ==========================================
    # Sub-rutas específicas del dominio de K-Pilot
    # ==========================================

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kpilot_memory.db"

    @property
    def base_samples_dir(self) -> Path:
        return self.data_dir / "base_samples"

    @property
    def core_learned_dir(self) -> Path:
        return self.data_dir / "core_learned"

    @property
    def hard_negatives_dir(self) -> Path:
        return self.data_dir / "hard_negatives"

    @property
    def temp_audio_path(self) -> Path:
        """Audio temporal que se envía al STT/Gemini."""
        return self.cache_dir / "ultimo_comando.wav"

    def ensure_directories(self) -> None:
        """Crea todo el árbol de directorios si no existe al iniciar la app."""
        directories = [
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.base_samples_dir,
            self.core_learned_dir,
            self.hard_negatives_dir,
        ]
        for directory in directories:
            # parents=True crea las carpetas intermedias si faltan
            # exist_ok=True evita errores si ya existen
            directory.mkdir(parents=True, exist_ok=True)


# Singleton global para importar fácilmente
paths = AppPaths()
