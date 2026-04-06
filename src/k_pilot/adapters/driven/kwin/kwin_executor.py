import asyncio
from collections.abc import Sequence
from typing import ClassVar, Final

from k_pilot.adapters.driven.kwin.kwin_types import KdotoolResult
from k_pilot.core.shared.logging import get_logger


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

        logger = get_logger("k-pilot.kwin_executor.init")

        logger.debug(
            "initialized",
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

        logger = get_logger("k-pilot.kwin_executor")

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

        logger = get_logger("k-pilot.kwin_executor")

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
            except TimeoutError:
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
