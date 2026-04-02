# Copyright 2026 K-Pilot Contributors
# SPDX-License-Identifier: LGPL-2.1-or-later
# pylint: disable=too-many-public-methods

"""
KWin window manager adapter via kdotool for KDE Plasma 6.

This module provides production-grade window management for KDE Plasma
environments using the kdotool CLI (v0.2.2+). Supports both Wayland and X11
sessions through a unified interface.

Requirements:
    - kdotool >= 0.2.2 (https://github.com/jinliu/kdotool)
    - KDE Plasma 5/6

References:
    - kdotool documentation: https://github.com/jinliu/kdotool
    - KWin scripting: https://develop.kde.org/docs/plasma/kwin/
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Final

import structlog

from k_pilot.domain.models import Result, WindowInfo
from k_pilot.domain.ports import WindowManagerPort

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = structlog.get_logger("k-pilot.kwin_adapter")


class KWinAdapterError(Exception):
    """Base exception for KWin adapter operations."""

    def __init__(
        self,
        message: str,
        *,
        command: str | None = None,
        window_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.window_id = window_id


class KdotoolNotFoundError(KWinAdapterError):
    """Raised when kdotool executable is not available."""

    pass


class WindowOperationError(KWinAdapterError):
    """Raised when a window operation fails."""

    pass


class KdotoolCommand(StrEnum):
    """
    Available kdotool commands.

    See: https://github.com/jinliu/kdotool#commands
    """

    SEARCH = "search"
    GET_ACTIVE_WINDOW = "getactivewindow"
    GET_WINDOW_NAME = "getwindowname"
    GET_WINDOW_CLASS = "getwindowclassname"
    GET_DESKTOP = "get_desktop_for_window"
    SET_DESKTOP = "set_desktop_for_window"
    WINDOW_ACTIVATE = "windowactivate"
    WINDOW_MINIMIZE = "windowminimize"
    WINDOW_STATE = "windowstate"
    WINDOW_CLOSE = "windowclose"


@dataclass(frozen=True, slots=True)
class KdotoolResult:
    """
    Immutable result container for kdotool execution.

    Attributes:
        success: Whether the command executed successfully.
        stdout: Standard output from the command.
        stderr: Standard error output.
        return_code: Process return code.
    """

    success: bool
    stdout: str
    stderr: str = ""
    return_code: int | None = 0

    @property
    def output(self) -> str:
        """Convenience property for stdout stripped."""
        return self.stdout.strip()


class KdotoolExecutor:
    """
    Thread-safe executor for kdotool CLI operations.

    Handles process execution with semaphore-based concurrency control,
    timeout handling, and retry logic for transient failures.

    Args:
        executable_path: Path to kdotool binary.
        max_concurrent: Maximum concurrent kdotool processes (default: 3).
        default_timeout: Default timeout in seconds (default: 5).
    """

    DEFAULT_TIMEOUT: ClassVar[int] = 5
    MAX_RETRIES: ClassVar[int] = 1
    RETRY_DELAY: ClassVar[float] = 0.1

    def __init__(
        self,
        executable_path: str,
        max_concurrent: int = 3,
        default_timeout: int = 5,
    ) -> None:
        self._executable: Final[str] = executable_path
        self._semaphore: Final[asyncio.Semaphore] = asyncio.Semaphore(max_concurrent)
        self._default_timeout: Final[int] = default_timeout

        logger.debug(
            "kdotool.executor.initialized",
            executable=executable_path,
            max_concurrent=max_concurrent,
        )

    async def execute(
        self,
        *args: str,
        timeout: int | None = None,
        retries: int = 1,
    ) -> KdotoolResult:
        """
        Execute kdotool command with retry logic.

        Automatically retries on specific transient errors like D-Bus object
        path not found, which can occur during rapid window operations.

        Args:
            *args: Command arguments (not including 'kdotool' itself).
            timeout: Override default timeout in seconds.
            retries: Number of retry attempts for transient failures.

        Returns:
            KdotoolResult with execution details.

        Raises:
            KdotoolNotFoundError: If kdotool is not available at init time.
        """
        timeout = timeout or self._default_timeout
        last_result = KdotoolResult(success=False, stdout="", stderr="Unknown error")

        async with self._semaphore:
            for attempt in range(retries + 1):
                result = await self._execute_once(args, timeout)

                if result.success:
                    return result

                # Check for transient D-Bus errors that warrant retry
                if "No such object path" in result.stderr and attempt < retries:
                    logger.warning(
                        "kdotool.transient_error",
                        args=args,
                        attempt=attempt + 1,
                        delay=self.RETRY_DELAY,
                    )
                    await asyncio.sleep(0.1)
                    last_result = result
                    continue

                return result

        return last_result

    async def _execute_once(
        self,
        args: Sequence[str],
        timeout: int,
    ) -> KdotoolResult:
        """Execute single kdotool invocation."""
        cmd = [self._executable, *args]
        logger.debug("kdotool.exec", command=" ".join(cmd))

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                logger.error("kdotool.timeout", command=cmd, timeout=timeout)
                return KdotoolResult(
                    success=False,
                    stdout="",
                    stderr=f"Timeout after {timeout}s",
                    return_code=-1,
                )

            stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
            stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
            success = process.returncode == 0

            if success:
                logger.debug(
                    "kdotool.success",
                    command=cmd[0] if cmd else "unknown",
                    output=stdout[:80],
                )
            else:
                logger.warning(
                    "kdotool.error",
                    command=cmd,
                    stderr=stderr,
                    code=process.returncode,
                )

            return KdotoolResult(
                success=success,
                stdout=stdout,
                stderr=stderr,
                return_code=process.returncode,
            )

        except FileNotFoundError:
            logger.error("kdotool.not_found", executable=self._executable)
            return KdotoolResult(
                success=False,
                stdout="",
                stderr=f"kdotool not found: {self._executable}",
                return_code=-1,
            )
        except Exception as exc:
            logger.exception("kdotool.exception", error=str(exc))
            return KdotoolResult(
                success=False,
                stdout="",
                stderr=str(exc),
                return_code=-1,
            )


class KWinWindowAdapter(WindowManagerPort):
    """
    Production-grade KWin window manager adapter.

    Implements WindowManagerPort using kdotool for KDE Plasma environments.
    Provides comprehensive window management with proper error handling,
    concurrency control, and Plasma 6 Wayland compatibility.

    Thread Safety:
        This adapter is asyncio-safe. All public methods are coroutines and
        use internal semaphore to limit concurrent kdotool processes.

    Example:
        >>> adapter = KWinWindowAdapter()
        >>> if adapter.is_available():
        ...     windows = await adapter.list_windows()
        ...     for win in windows:
        ...         print(f"{win.title} on desktop {win.desktop}")
    """

    # kdotool version compatibility
    KDOTOOL_MIN_VERSION: ClassVar[str] = "0.2.2"
    MAX_CONCURRENT_OPS: ClassVar[int] = 3
    DEFAULT_TIMEOUT: ClassVar[int] = 5

    # Window ID validation
    WINDOW_ID_PREFIX: ClassVar[str] = "{"

    def __init__(self, bus: object | None = None) -> None:
        """
        Initialize KWin adapter.

        Args:
            bus: Unused, kept for interface compatibility with other adapters.
        """
        self._executable: str | None = shutil.which("kdotool")
        self._available: bool = self._executable is not None
        self._executor: KdotoolExecutor | None = None

        if self._available and self._executable:
            self._executor = KdotoolExecutor(
                executable_path=self._executable,
                max_concurrent=3,
                default_timeout=5,
            )
            logger.info(
                "kwin_adapter.initialized",
                version=self.KDOTOOL_MIN_VERSION,
                backend="kdotool",
                executable=self._executable,
            )
        else:
            logger.error(
                "kwin_adapter.not_found",
                executable="kdotool",
                hint="Install kdotool from https://github.com/jinliu/kdotool",
            )

    def is_available(self) -> bool:
        """
        Check if kdotool is installed and accessible.

        Returns:
            True if kdotool binary was found in PATH.
        """
        return self._available

    async def list_windows(self) -> list[WindowInfo]:
        """
        List all managed windows currently visible to kdotool.

        Retrieves window list with metadata including title, application class,
        desktop assignment, and active status. Results are sorted by:
        1. Active window first
        2. Desktop number (ascending)
        3. Window title (alphabetical)

        Returns:
            List of WindowInfo objects. Empty list if kdotool unavailable
            or no windows found.
        """
        if not self._available or not self._executor:
            logger.warning("window.list.unavailable")
            return []

        logger.info("window.list.started")

        # Search for all windows (limit 50 for performance)
        result = await self._executor.execute(
            KdotoolCommand.SEARCH,
            "--class",
            ".",
            "--limit",
            "50",
            retries=1,
        )

        if not result.success or not result.output:
            logger.warning(
                "window.list.empty_source",
                success=result.success,
                stderr=result.stderr[:100],
            )
            return []

        window_ids = [
            line.strip()
            for line in result.output.splitlines()
            if line.strip().startswith("{")
        ]

        if not window_ids:
            logger.info("window.list.no_valid_ids")
            return []

        # Get active window ID for comparison
        active_result = await self._executor.execute(
            KdotoolCommand.GET_ACTIVE_WINDOW,
            retries=0,
        )
        active_id = active_result.output if active_result.success else ""

        # Fetch info for all windows concurrently
        tasks = [self._get_window_info_safe(wid, active_id) for wid in window_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        windows: list[WindowInfo] = []
        for result in results:
            if isinstance(result, WindowInfo):
                windows.append(result)
            elif isinstance(result, Exception):
                logger.error("window.info_failed", error=str(result))

        # Sort: Active first, then by desktop, then by title
        windows.sort(
            key=lambda w: (
                0 if w.is_active else 1,
                w.desktop if w.desktop is not None else 9999,
                w.title.lower(),
            )
        )

        logger.info("window.list.completed", count=len(windows))
        return windows

    async def get_active_window(self) -> WindowInfo | None:
        """
        Get information about the currently focused window.

        Returns:
            WindowInfo for active window, or None if no window is focused
            or operation fails.
        """
        if not self._executor:
            return None

        logger.debug("window.get_active.started")

        wid_result = await self._executor.execute(
            KdotoolCommand.GET_ACTIVE_WINDOW,
            retries=0,
        )

        if not wid_result.success or not wid_result.output:
            logger.warning("window.get_active.failed", stderr=wid_result.stderr)
            return None

        window_id = wid_result.output

        # Fetch window details concurrently
        name_task = self._executor.execute(
            KdotoolCommand.GET_WINDOW_NAME, window_id, retries=0
        )
        class_task = self._executor.execute(
            KdotoolCommand.GET_WINDOW_CLASS, window_id, retries=0
        )
        desk_task = self._executor.execute(
            KdotoolCommand.GET_DESKTOP, window_id, retries=0
        )

        name_result, class_result, desk_result = await asyncio.gather(
            name_task, class_task, desk_task
        )

        desktop = self._parse_desktop_output(desk_result.output)

        window = WindowInfo(
            id=window_id,
            title=name_result.output if name_result.success else "Unknown",
            app_name=class_result.output if class_result.success else "unknown",
            is_active=True,
            is_minimized=False,
            desktop=desktop,
        )

        logger.info("window.get_active.completed", window_id=window_id)
        return window

    async def focus_window(self, window_id: str) -> Result:
        """
        Activate/focus the specified window.

        Args:
            window_id: kdotool window identifier (e.g., '{12345678-1234...}').

        Returns:
            Result indicating success with window_id in data.

        Raises:
            WindowOperationError: If the window cannot be activated.
        """
        if not self._executor:
            return Result(False, "kdotool not available")

        logger.info("window.focus.started", window_id=window_id)

        result = await self._executor.execute(
            KdotoolCommand.WINDOW_ACTIVATE,
            window_id,
            retries=1,
        )

        if result.success:
            logger.info("window.focus.completed", window_id=window_id)
            return Result(
                success=True,
                message="Window activated",
                data={"window_id": window_id},
            )

        logger.warning("window.focus.failed", window_id=window_id, error=result.stderr)
        return Result(
            success=False,
            message=f"Failed to activate window: {result.stderr}",
        )

    async def minimize_window(self, window_id: str) -> Result:
        """
        Minimize the specified window.

        Args:
            window_id: kdotool window identifier.

        Returns:
            Result indicating success.
        """
        return await self._simple_window_operation(
            window_id=window_id,
            command=KdotoolCommand.WINDOW_MINIMIZE,
            success_message="Window minimized",
            operation_name="minimize",
        )

    async def maximize_window(self, window_id: str) -> Result:
        """
        Set window to fullscreen/maximized state.

        Note: In Plasma Wayland, this uses FULLSCREEN state as kwin's
        maximize behavior differs between X11 and Wayland.

        Args:
            window_id: kdotool window identifier.

        Returns:
            Result indicating success.
        """
        if not self._executor:
            return Result(False, "kdotool not available")

        logger.info("window.maximize.started", window_id=window_id)

        # Note: In Wayland, we use FULLSCREEN as it's more reliable than
        # traditional maximize across different window types
        result = await self._executor.execute(
            KdotoolCommand.WINDOW_STATE,
            "--add",
            "FULLSCREEN",
            window_id,
        )

        if result.success:
            logger.info("window.maximize.completed", window_id=window_id)
            return Result(
                success=True,
                message="Window maximized (fullscreen)",
                data={
                    "window_id": window_id,
                    "state": "fullscreen",
                    "note": "Uses fullscreen due to Wayland compatibility",
                },
            )

        return Result(
            success=False,
            message=f"Failed to maximize: {result.stderr}",
        )

    async def close_window(self, window_id: str) -> Result:
        """
        Close the specified window.

        Args:
            window_id: kdotool window identifier.

        Returns:
            Result indicating success.
        """
        return await self._simple_window_operation(
            window_id=window_id,
            command=KdotoolCommand.WINDOW_CLOSE,
            success_message="Window closed",
            operation_name="close",
        )

    async def set_window_desktop(
        self,
        window_id: str,
        desktop: int | str,
    ) -> Result:
        """
        Move window to specified virtual desktop.

        Args:
            window_id: kdotool window identifier.
            desktop: Desktop number (1-based) or "all" for all desktops.

        Returns:
            Result indicating success.
        """
        if not self._executor:
            return Result(False, "kdotool not available")

        desktop_str = str(desktop)
        logger.info(
            "window.set_desktop.started",
            window_id=window_id,
            desktop=desktop_str,
        )

        result = await self._executor.execute(
            KdotoolCommand.SET_DESKTOP,
            window_id,
            desktop_str,
        )

        if result.success:
            return Result(
                success=True,
                message=f"Window moved to desktop {desktop_str}",
                data={"window_id": window_id, "desktop": desktop_str},
            )

        return Result(False, result.stderr)

    async def set_always_on_top(
        self,
        window_id: str,
        enabled: bool = True,
    ) -> Result:
        """
        Toggle window's always-on-top (above) state.

        Args:
            window_id: kdotool window identifier.
            enabled: True to enable always-on-top, False to disable.

        Returns:
            Result indicating success.
        """
        if not self._executor:
            return Result(False, "kdotool not available")

        action = "--add" if enabled else "--remove"
        state_str = "enabled" if enabled else "disabled"

        result = await self._executor.execute(
            KdotoolCommand.WINDOW_STATE,
            action,
            "ABOVE",
            window_id,
        )

        if result.success:
            return Result(
                success=True,
                message=f"Always on top {state_str}",
                data={
                    "window_id": window_id,
                    "always_on_top": enabled,
                },
            )

        return Result(False, result.stderr)

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    async def _get_window_info_safe(
        self,
        window_id: str,
        active_id: str,
    ) -> WindowInfo:
        """
        Safely retrieve window info, returning partial data on failure.

        This method is designed to be used in gather() where individual
        failures shouldn't break the entire window list operation.

        Args:
            window_id: Window identifier.
            active_id: Currently active window ID for comparison.

        Returns:
            WindowInfo with best-effort data (falls back to defaults on error).
        """
        if not self._executor:
            logger.error(
                "window.info_exception", id=window_id, error="Executor not initialized"
            )
            # Return placeholder to avoid breaking list_windows()
            return WindowInfo(
                id=window_id,
                title="Error retrieving info",
                app_name="unknown",
                is_active=False,
                is_minimized=False,
                desktop=None,
            )
        try:
            # Fetch all properties concurrently
            name_task = self._executor.execute(
                KdotoolCommand.GET_WINDOW_NAME, window_id
            )
            class_task = self._executor.execute(
                KdotoolCommand.GET_WINDOW_CLASS, window_id
            )
            desk_task = self._executor.execute(KdotoolCommand.GET_DESKTOP, window_id)

            name_result, class_result, desk_result = await asyncio.gather(
                name_task, class_task, desk_task
            )

            title = (
                name_result.output
                if name_result.success and name_result.output
                else "Unknown"
            )
            app_name = (
                class_result.output
                if class_result.success and class_result.output
                else "unknown"
            )
            desktop = self._parse_desktop_output(desk_result.output)

            return WindowInfo(
                id=window_id,
                title=title,
                app_name=app_name,
                is_active=(window_id == active_id),
                is_minimized=False,  # kdotool doesn't expose this directly
                desktop=desktop,
            )

        except Exception as exc:
            logger.error("window.info_exception", id=window_id, error=str(exc))
            # Return placeholder to avoid breaking list_windows()
            return WindowInfo(
                id=window_id,
                title="Error retrieving info",
                app_name="unknown",
                is_active=False,
                is_minimized=False,
                desktop=None,
            )

    async def _simple_window_operation(
        self,
        window_id: str,
        command: KdotoolCommand,
        success_message: str,
        operation_name: str,
    ) -> Result:
        """Execute simple single-command window operation."""
        if not self._executor:
            return Result(False, "kdotool not available")

        logger.info(f"window.{operation_name}.started", window_id=window_id)

        result = await self._executor.execute(command, window_id)

        if result.success:
            logger.info(
                f"window.{operation_name}.completed",
                window_id=window_id,
            )
            return Result(
                success=True,
                message=success_message,
                data={"window_id": window_id},
            )

        logger.warning(
            f"window.{operation_name}.failed",
            window_id=window_id,
            error=result.stderr,
        )
        return Result(False, result.stderr)

    @staticmethod
    def _parse_desktop_output(output: str) -> int | None:
        """
        Parse desktop number from kdotool output.

        Args:
            output: Raw string output from get_desktop_for_window.

        Returns:
            Desktop number (0-based or 1-based depending on Plasma config),
            or None for "all desktops" or invalid output.
        """
        if not output or output.lower() == "null":
            return None
        try:
            return int(output)
        except ValueError:
            return None
