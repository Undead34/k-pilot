from dataclasses import dataclass
from enum import StrEnum


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
