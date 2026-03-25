"""Adapter para KWin usando kdotool v0.2.2 (KDE Plasma 6 Wayland)."""

import asyncio
import shutil
import subprocess
from typing import Optional

import structlog

from k_pilot.domain.models import Result, WindowInfo
from k_pilot.domain.ports import WindowManagerPort

logger = structlog.get_logger()

"""Adapter para KWin usando kdotool v0.2.2 (KDE Plasma 6 Wayland)."""


class KWinWindowAdapter(WindowManagerPort):
    """
    Control nativo de ventanas KDE vía kdotool.
    Compatible con Plasma 5/6, Wayland y X11.
    """

    def __init__(self, bus=None):
        self._kdotool = shutil.which("kdotool")
        self._available = self._kdotool is not None
        # Limitar concurrencia a 3 llamadas simultáneas para no saturar KWin
        self._semaphore = asyncio.Semaphore(3)

        if self._available:
            logger.info("kwin_adapter.initialized", version="0.2.2")
        else:
            logger.error("kwin_adapter.not_found")

    def is_available(self) -> bool:
        return self._available

    def _run(
        self, *args, timeout: int = 5, check_output: bool = True
    ) -> tuple[bool, str]:
        """Ejecuta kdotool y retorna (éxito, stdout/stderr)."""
        if not self._available:
            return False, "kdotool no instalado"

        cmd = [self._kdotool, *args]
        logger.debug("kdotool.exec", cmd=" ".join(cmd))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )

            if result.returncode != 0:
                logger.warning(
                    "kdotool.error",
                    args=args,
                    stderr=result.stderr.strip(),
                    code=result.returncode,
                )
                return False, result.stderr.strip()

            output = result.stdout.strip()
            logger.debug("kdotool.success", args=args, output=output[:50])
            return True, output

        except subprocess.TimeoutExpired:
            logger.error("kdotool.timeout", args=args)
            return False, "Timeout"
        except Exception as e:
            logger.error("kdotool.exception", error=str(e))
            return False, str(e)

    async def _run_with_retry(self, *args, retries: int = 1) -> tuple[bool, str]:
        """Ejecuta con reintentos para errores de Scripting."""
        async with self._semaphore:  # Limitar concurrencia
            for attempt in range(retries + 1):
                success, output = await self._run_async(*args)
                if success or "No such object path" not in output:
                    return success, output
                if attempt < retries:
                    logger.warning("kdotool.retry", args=args, attempt=attempt + 1)
                    await asyncio.sleep(0.1)  # Pequeña pausa antes de reintentar
            return success, output

    def _run_async(self, *args, timeout: int = 5):
        """Wrapper async para subprocess."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, lambda: self._run(*args, timeout=timeout))

    async def list_windows(self) -> list[WindowInfo]:
        """Versión async con concurrencia controlada."""
        if not self._kdotool:
            return []

        # 1. Obtener lista de IDs
        success, stdout = await self._run_with_retry(
            "search", "--class", ".", "--limit", "50"
        )
        if not success or not stdout:
            return []

        uuids = [
            line.strip()
            for line in stdout.split("\n")
            if line.strip() and line.strip().startswith("{")
        ]

        # 2. Obtener active window UNA SOLA VEZ
        _, active_id = await self._run_with_retry("getactivewindow")

        # 3. Procesar en paralelo pero controlado (max 3 a la vez)
        tasks = [self._get_window_info_safe(wid, active_id) for wid in uuids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        windows = []
        for r in results:
            if isinstance(r, WindowInfo):
                windows.append(r)
            elif isinstance(r, Exception):
                logger.error("window.info_failed", error=str(r))

        windows.sort(key=lambda w: (0 if w.is_active else 1, w.desktop))
        return windows

    async def _get_window_info_safe(self, wid: str, active_id: str) -> WindowInfo:
        """Obtiene info de ventana con manejo de errores."""
        try:
            # Usar gather pero con el semáforo interno ya limitado
            name_task = self._run_with_retry("getwindowname", wid)
            class_task = self._run_with_retry("getwindowclassname", wid)
            desk_task = self._run_with_retry("get_desktop_for_window", wid)

            (
                (ok_name, title),
                (ok_class, app_name),
                (ok_desk, desk_str),
            ) = await asyncio.gather(name_task, class_task, desk_task)

            title = title if ok_name else "Desconocido"
            app_name = app_name if ok_class else "desconocido"

            try:
                desktop = int(desk_str) if ok_desk and desk_str != "null" else -1
            except ValueError:
                desktop = -1

            return WindowInfo(
                id=wid,
                title=title,
                app_name=app_name,
                is_active=wid == active_id,
                is_minimized=False,
                desktop=desktop,
            )
        except Exception as e:
            logger.error("window.info_exception", id=wid, error=str(e))
            # Retornar ventana vacía en caso de error para no romper el listado
            return WindowInfo(
                id=wid,
                title="Error al obtener",
                app_name="desconocido",
                is_active=False,
                is_minimized=False,
                desktop=-1,
            )

    def focus_window(self, window_id: str) -> Result:
        """Activa la ventana."""
        success, msg = self._run("windowactivate", window_id)
        if success:
            return Result(True, "Ventana activada")
        return Result(False, f"No se pudo activar: {msg}")

    def minimize_window(self, window_id: str) -> Result:
        """Minimiza la ventana."""
        success, msg = self._run("windowminimize", window_id)
        if success:
            return Result(True, "Ventana minimizada")
        return Result(False, msg)

    def maximize_window(self, window_id: str) -> Result:
        """Maximiza ventana."""
        success, msg = self._run("windowstate", "--add", "FULLSCREEN", window_id)
        if success:
            return Result(True, "Ventana maximizada (fullscreen)")
        return Result(False, f"No se pudo maximizar: {msg}")

    def close_window(self, window_id: str) -> Result:
        """Cierra la ventana."""
        success, msg = self._run("windowclose", window_id)
        if success:
            return Result(True, "Ventana cerrada")
        return Result(False, msg)

    def get_active_window(self) -> Optional[WindowInfo]:
        """Obtiene la ventana actualmente enfocada."""
        success, wid = self._run("getactivewindow", timeout=2)
        if not success or not wid:
            return None

        success_name, title = self._run("getwindowname", wid, timeout=2)
        title = title if success_name else "Desconocido"

        success_class, app_name = self._run("getwindowclassname", wid, timeout=2)
        app_name = app_name if success_class else "desconocido"

        return WindowInfo(
            id=wid,
            title=title,
            app_name=app_name,
            is_active=True,
            is_minimized=False,
            desktop=-1,
        )

    def set_window_desktop(self, window_id: str, desktop: int | str) -> Result:
        """Mueve ventana a otro escritorio."""
        desktop_str = str(desktop) if isinstance(desktop, int) else desktop
        success, msg = self._run("set_desktop_for_window", window_id, desktop_str)
        if success:
            return Result(True, f"Ventana movida al escritorio {desktop_str}")
        return Result(False, msg)

    def set_always_on_top(self, window_id: str, enabled: bool = True) -> Result:
        """Marca ventana como 'siempre encima'."""
        action = "--add" if enabled else "--remove"
        success, msg = self._run("windowstate", action, "ABOVE", window_id)
        if success:
            state = "activado" if enabled else "desactivado"
            return Result(True, f"Siempre encima {state}")
        return Result(False, msg)
