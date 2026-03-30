"""Adapter para KWin usando kdotool v0.2.2 (KDE Plasma 6 Wayland)."""

import asyncio
import shutil
import subprocess

from k_pilot.domain.models import Result, WindowInfo
from k_pilot.domain.ports import WindowManagerPort
from k_pilot.infrastructure.logging import get_logger

logger = get_logger(layer="infrastructure", component="kwin_adapter")


class KWinWindowAdapter(WindowManagerPort):
    """
    Control nativo de ventanas KDE vía kdotool.
    Compatible con Plasma 5/6, Wayland y X11.
    """

    def __init__(self, bus=None):
        self._kdotool = shutil.which("kdotool")
        self._available = self._kdotool is not None
        self._semaphore = asyncio.Semaphore(3)

        if self._available:
            logger.info("kwin_adapter.initialized", version="0.2.2", backend="kdotool")
        else:
            logger.error("kwin_adapter.not_found", executable="kdotool")

    def is_available(self) -> bool:
        return self._available

    def _run(self, *args, timeout: int = 5) -> tuple[bool, str]:
        """Ejecuta kdotool y retorna (éxito, stdout/stderr)."""
        if not self._available or not self._kdotool:
            return False, "kdotool no instalado"

        cmd = [self._kdotool, *args]
        logger.debug("kdotool.exec", cmd=" ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                logger.warning(
                    "kdotool.error",
                    args=args,
                    stderr=stderr,
                    code=result.returncode,
                )
                return False, stderr

            output = result.stdout.strip()
            logger.debug("kdotool.success", args=args, output=output[:80])
            return True, output

        except subprocess.TimeoutExpired:
            logger.error("kdotool.timeout", args=args)
            return False, "Timeout"
        except Exception as e:
            logger.error("kdotool.exception", error=str(e))
            return False, str(e)

    async def _run_async(self, *args, timeout: int = 5) -> tuple[bool, str]:
        """Wrapper async para subprocess.run."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, lambda: self._run(*args, timeout=timeout)
        )

    async def _run_with_retry(self, *args, retries: int = 1) -> tuple[bool, str]:
        """Ejecuta con reintentos para errores transitorios de scripting/DBus."""
        success, output = False, ""

        async with self._semaphore:
            for attempt in range(retries + 1):
                success, output = await self._run_async(*args)
                if success or "No such object path" not in output:
                    return success, output

                if attempt < retries:
                    logger.warning("kdotool.retry", args=args, attempt=attempt + 1)
                    await asyncio.sleep(0.1)

        return success, output

    async def list_windows(self) -> list[WindowInfo]:
        """Lista ventanas visibles/gestionadas por KWin."""
        if not self._available:
            logger.warning("window_port.unavailable")
            return []

        logger.info("window_port.list.started")

        success, stdout = await self._run_with_retry(
            "search", "--class", ".", "--limit", "50"
        )
        if not success or not stdout:
            logger.warning("window_port.list.empty_source", success=success)
            return []

        window_ids = [
            line.strip()
            for line in stdout.splitlines()
            if line.strip() and line.strip().startswith("{")
        ]

        _, active_id = await self._run_with_retry("getactivewindow")

        tasks = [self._get_window_info_safe(wid, active_id) for wid in window_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        windows: list[WindowInfo] = []
        for result in results:
            if isinstance(result, WindowInfo):
                windows.append(result)
            elif isinstance(result, Exception):
                logger.error("window.info_failed", error=str(result))

        windows.sort(
            key=lambda w: (
                0 if w.is_active else 1,
                w.desktop if w.desktop is not None else 9999,
                w.title,
            )
        )
        logger.info("window_port.list.completed", count=len(windows))
        return windows

    async def _get_window_info_safe(self, wid: str, active_id: str) -> WindowInfo:
        """Obtiene la información de una ventana sin romper el listado si falla."""
        try:
            name_task = self._run_with_retry("getwindowname", wid)
            class_task = self._run_with_retry("getwindowclassname", wid)
            desk_task = self._run_with_retry("get_desktop_for_window", wid)

            (
                (ok_name, title),
                (ok_class, app_name),
                (ok_desk, desk_str),
            ) = await asyncio.gather(name_task, class_task, desk_task)

            title = title if ok_name and title else "Desconocido"
            app_name = app_name if ok_class and app_name else "desconocido"

            desktop: int | None = None
            if ok_desk and desk_str and desk_str != "null":
                try:
                    desktop = int(desk_str)
                except ValueError:
                    desktop = None

            return WindowInfo(
                id=wid,
                title=title,
                app_name=app_name,
                is_active=(wid == active_id),
                is_minimized=False,
                desktop=desktop,
            )

        except Exception as e:
            logger.error("window.info_exception", id=wid, error=str(e))
            return WindowInfo(
                id=wid,
                title="Error al obtener",
                app_name="desconocido",
                is_active=False,
                is_minimized=False,
                desktop=None,
            )

    async def get_active_window(self) -> WindowInfo | None:
        """Obtiene la ventana actualmente activa."""
        logger.debug("window_port.get_active.started")
        success, wid = await self._run_with_retry("getactivewindow", retries=0)
        if not success or not wid:
            logger.warning("window_port.get_active.failed")
            return None

        success_name, title = await self._run_with_retry(
            "getwindowname", wid, retries=0
        )
        success_class, app_name = await self._run_with_retry(
            "getwindowclassname", wid, retries=0
        )
        success_desk, desk_str = await self._run_with_retry(
            "get_desktop_for_window", wid, retries=0
        )

        desktop: int | None = None
        if success_desk and desk_str and desk_str != "null":
            try:
                desktop = int(desk_str)
            except ValueError:
                desktop = None

        window = WindowInfo(
            id=wid,
            title=title if success_name and title else "Desconocido",
            app_name=app_name if success_class and app_name else "desconocido",
            is_active=True,
            is_minimized=False,
            desktop=desktop,
        )
        logger.info("window_port.get_active.completed", window_id=wid)
        return window

    async def focus_window(self, window_id: str) -> Result:
        """Activa la ventana."""
        logger.info("window_port.focus.started", window_id=window_id)
        success, msg = await self._run_with_retry("windowactivate", window_id)
        if success:
            logger.info(
                "window_port.focus.completed", window_id=window_id, success=True
            )
            return Result(True, "Ventana activada", {"window_id": window_id})
        logger.warning(
            "window_port.focus.completed", window_id=window_id, success=False
        )
        return Result(False, f"No se pudo activar: {msg}")

    async def minimize_window(self, window_id: str) -> Result:
        """Minimiza la ventana."""
        logger.info("window_port.minimize.started", window_id=window_id)
        success, msg = await self._run_with_retry("windowminimize", window_id)
        if success:
            logger.info(
                "window_port.minimize.completed", window_id=window_id, success=True
            )
            return Result(True, "Ventana minimizada", {"window_id": window_id})
        logger.warning(
            "window_port.minimize.completed", window_id=window_id, success=False
        )
        return Result(False, msg)

    async def maximize_window(self, window_id: str) -> Result:
        """Maximiza la ventana."""
        logger.info("window_port.maximize.started", window_id=window_id)
        success, msg = await self._run_with_retry(
            "windowstate", "--add", "FULLSCREEN", window_id
        )
        if success:
            logger.info(
                "window_port.maximize.completed", window_id=window_id, success=True
            )
            return Result(
                True,
                "Ventana maximizada (fullscreen)",
                {"window_id": window_id, "state": "fullscreen"},
            )
        logger.warning(
            "window_port.maximize.completed", window_id=window_id, success=False
        )
        return Result(False, f"No se pudo maximizar: {msg}")

    async def close_window(self, window_id: str) -> Result:
        """Cierra la ventana."""
        logger.info("window_port.close.started", window_id=window_id)
        success, msg = await self._run_with_retry("windowclose", window_id)
        if success:
            logger.info(
                "window_port.close.completed", window_id=window_id, success=True
            )
            return Result(True, "Ventana cerrada", {"window_id": window_id})
        logger.warning(
            "window_port.close.completed", window_id=window_id, success=False
        )
        return Result(False, msg)

    async def set_window_desktop(self, window_id: str, desktop: int | str) -> Result:
        """Mueve una ventana a otro escritorio."""
        desktop_str = str(desktop)
        success, msg = await self._run_with_retry(
            "set_desktop_for_window", window_id, desktop_str
        )
        if success:
            return Result(
                True,
                f"Ventana movida al escritorio {desktop_str}",
                {"window_id": window_id, "desktop": desktop_str},
            )
        return Result(False, msg)

    async def set_always_on_top(self, window_id: str, enabled: bool = True) -> Result:
        """Activa o desactiva el modo 'siempre encima'."""
        action = "--add" if enabled else "--remove"
        success, msg = await self._run_with_retry(
            "windowstate", action, "ABOVE", window_id
        )
        if success:
            state = "activado" if enabled else "desactivado"
            return Result(
                True,
                f"Siempre encima {state}",
                {"window_id": window_id, "always_on_top": enabled},
            )
        return Result(False, msg)
