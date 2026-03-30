import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from typing import Any

from k_pilot.domain.wayland import WindowInfo, WindowRect


class KWinWaylandWindowProvider:
    def __init__(self, qdbus_path: str | None = None):
        self.qdbus_path = (
            qdbus_path
            or shutil.which("qdbus6")
            or shutil.which("qdbus-qt6")
            or shutil.which("qdbus")
        )

    @staticmethod
    def is_kde_wayland_session() -> bool:
        desktop = (os.environ.get("XDG_CURRENT_DESKTOP") or "").upper()
        session_type = (os.environ.get("XDG_SESSION_TYPE") or "").lower()
        return session_type == "wayland" and "KDE" in desktop

    def ensure_available(self) -> None:
        if not self.is_kde_wayland_session():
            raise RuntimeError("Esta clase solo soporta KDE Plasma sobre Wayland")
        if not self.qdbus_path:
            raise RuntimeError("No se encontró qdbus/qdbus6 para hablar con KWin")

    def list_windows(self) -> list[WindowInfo]:
        self.ensure_available()
        payload = self._run_snapshot_script()
        return [self._parse_window(item) for item in payload]

    def get_active_window(self) -> WindowInfo | None:
        for win in self.list_windows():
            if win.active:
                return win
        return None

    def find_by_title(self, title: str) -> WindowInfo | None:
        title_cf = title.casefold()
        for win in self.list_windows():
            if title_cf in win.title.casefold():
                return win
        return None

    def find_by_window_id(self, window_id: str) -> WindowInfo | None:
        for win in self.list_windows():
            if win.window_id == window_id:
                return win
        return None

    def _parse_window(self, item: dict[str, Any]) -> WindowInfo:
        frame = item["frameGeometry"]
        client = item["clientGeometry"]

        # Instanciamos primero el Rect con la API limpia
        rect = WindowRect(
            x=float(client["x"]),
            y=float(client["y"]),
            width=float(client["width"]),
            height=float(client["height"]),
            window_x=float(frame["x"]),
            window_y=float(frame["y"]),
            window_width=float(frame["width"]),
            window_height=float(frame["height"]),
        )

        # Retornamos el Info empaquetado
        return WindowInfo(
            window_id=item["internalId"],
            title=item.get("caption") or "",
            rect=rect,
            app_name=item.get("app_name") or "",
            pid=item.get("pid"),
            active=bool(item.get("active", False)),
            minimized=bool(item.get("minimized", False)),
            maximized=bool(item.get("maximized", False)),
            fullscreen=bool(item.get("fullScreen", False)),
        )

    def _run_snapshot_script(self) -> list[dict[str, Any]]:
        # Añadí w.maximizeMode !== 0 para extraer el estado maximizado correctamente
        script = r"""
const result = [];
for (const w of workspace.stackingOrder) {
    if (!w || w.deleted || !w.managed) {
        continue;
    }

    result.push({
        internalId: String(w.internalId),
        caption: w.caption || "",
        app_name: String(w.resourceClass || w.resourceName || ""),
        pid: w.pid || null,
        active: !!w.active,
        minimized: !!w.minimized,
        maximized: w.maximizeMode !== 0,
        fullScreen: !!w.fullScreen,
        frameGeometry: {
            x: w.frameGeometry.x,
            y: w.frameGeometry.y,
            width: w.frameGeometry.width,
            height: w.frameGeometry.height
        },
        clientGeometry: {
            x: w.clientGeometry.x,
            y: w.clientGeometry.y,
            width: w.clientGeometry.width,
            height: w.clientGeometry.height
        }
    });
}
print(JSON.stringify(result));
"""
        return self._execute_kwin_script(script)

    def _execute_kwin_script(self, script: str) -> list[dict[str, Any]]:
        # Le garantizamos al linter que esto es un string, no None
        qdbus = self.qdbus_path
        if not qdbus:
            raise RuntimeError("qdbus no está disponible.")

        marker = f"KWIN_API_DUMP_{uuid.uuid4().hex}"
        plugin_name = f"python_bridge_{uuid.uuid4().hex[:8]}"

        patched_script = script.replace(
            "print(JSON.stringify(result));",
            f"console.info('{marker}::' + JSON.stringify(result));",
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
            f.write(patched_script)
            temp_path = f.name

        script_obj_path = None

        try:
            # Usamos la variable local 'qdbus' en lugar de 'self.qdbus_path'
            load_cmd = [
                qdbus,
                "org.kde.KWin",
                "/Scripting",
                "org.kde.kwin.Scripting.loadScript",
                temp_path,
                plugin_name,
            ]
            load_res = subprocess.run(
                load_cmd, capture_output=True, text=True, check=True
            )

            script_id = load_res.stdout.strip()
            if not script_id:
                raise RuntimeError("Falló la carga del script en KWin.")

            script_obj_path = f"/Scripting/Script{script_id}"

            subprocess.run(
                [qdbus, "org.kde.KWin", script_obj_path, "org.kde.kwin.Script.run"],
                check=True,
            )

            time.sleep(0.05)

            journal_cmd = [
                "journalctl",
                "--user",
                "_COMM=kwin_wayland",
                "--since",
                "10 seconds ago",
                "--output",
                "cat",
            ]

            for _ in range(5):
                journal_res = subprocess.run(
                    journal_cmd, capture_output=True, text=True
                )
                for line in reversed(journal_res.stdout.splitlines()):
                    if marker in line:
                        json_str = line.split(f"{marker}::")[-1]
                        return json.loads(json_str)
                time.sleep(0.1)

            raise RuntimeError(
                "Timeout: No se encontró la salida del script en el journal."
            )

        finally:
            if script_obj_path:
                subprocess.run(
                    [
                        qdbus,
                        "org.kde.KWin",
                        script_obj_path,
                        "org.kde.kwin.Script.stop",
                    ],
                    capture_output=True,
                    check=False,
                )

                subprocess.run(
                    [
                        qdbus,
                        "org.kde.KWin",
                        "/Scripting",
                        "org.kde.kwin.Scripting.unloadScript",
                        plugin_name,
                    ],
                    capture_output=True,
                    check=False,
                )

            if os.path.exists(temp_path):
                os.remove(temp_path)
