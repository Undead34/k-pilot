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
from typing import ClassVar

from k_pilot.adapters.driven.kwin.kwin_executor import KdotoolExecutor
from k_pilot.adapters.driven.kwin.kwin_types import KdotoolCommand
from k_pilot.core.application.ports.driven import WindowManagerPort
from k_pilot.core.domain import Result, WindowInfo
from k_pilot.core.shared.logging import get_logger


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

        logger = get_logger("k-pilot.kwin_adapter.init")

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
        logger = get_logger("k-pilot.kwin_adapter")

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
            line.strip() for line in result.output.splitlines() if line.strip().startswith("{")
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

        logger = get_logger("k-pilot.kwin_adapter")

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
        name_task = self._executor.execute(KdotoolCommand.GET_WINDOW_NAME, window_id, retries=0)
        class_task = self._executor.execute(KdotoolCommand.GET_WINDOW_CLASS, window_id, retries=0)
        desk_task = self._executor.execute(KdotoolCommand.GET_DESKTOP, window_id, retries=0)

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

        logger = get_logger("k-pilot.kwin_adapter")

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

        logger = get_logger("k-pilot.kwin_adapter")

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

        logger = get_logger("k-pilot.kwin_adapter")

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
        logger = get_logger("k-pilot.kwin_adapter")

        if not self._executor:
            logger.error("window.info_exception", id=window_id, error="Executor not initialized")
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
            name_task = self._executor.execute(KdotoolCommand.GET_WINDOW_NAME, window_id)
            class_task = self._executor.execute(KdotoolCommand.GET_WINDOW_CLASS, window_id)
            desk_task = self._executor.execute(KdotoolCommand.GET_DESKTOP, window_id)

            name_result, class_result, desk_result = await asyncio.gather(
                name_task, class_task, desk_task
            )

            title = name_result.output if name_result.success and name_result.output else "Unknown"
            app_name = (
                class_result.output if class_result.success and class_result.output else "unknown"
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

        logger = get_logger("k-pilot.kwin_adapter")

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
