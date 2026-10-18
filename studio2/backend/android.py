"""adb-адаптер: устройства, UI-дамп, скриншоты, запуск приложения."""
from __future__ import annotations

import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path


class AdbError(RuntimeError):
    pass


class AndroidDevice:
    def __init__(self, adb_path: str, serial: str | None = None):
        self.adb_path = adb_path
        self.serial = serial

    def _run(self, *args: str, timeout: int = 60) -> str:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                    errors="replace", timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            raise AdbError(f"adb недоступен: {exc}") from exc
        if result.returncode != 0:
            raise AdbError(result.stderr.strip() or f"adb {args[0]} завершился с ошибкой")
        return result.stdout

    def list_devices(self) -> list[dict]:
        out = self._run("devices", "-l")
        devices = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                devices.append({"serial": parts[0], "state": parts[1]})
        return devices

    def mode(self) -> str:
        return "live" if any(d["state"] == "device" for d in self.list_devices()) else "adb-only"

    def boot(self, emulator_path: str, avd: str) -> None:
        subprocess.Popen([emulator_path, "-avd", avd, "-no-snapshot-save"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait_ready(self, timeout: int = 180) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if self._run("shell", "getprop", "sys.boot_completed").strip() == "1":
                    return True
            except AdbError:
                pass
            time.sleep(3)
        return False

    def install(self, apk: str) -> None:
        self._run("install", "-r", "-t", str(apk), timeout=300)

    def launch(self, package: str) -> None:
        self._run("shell", "monkey", "-p", package,
                  "-c", "android.intent.category.LAUNCHER", "1")

    def dump_ui(self) -> dict:
        self._run("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        return parse_uiautomator_xml(self._run("shell", "cat", "/sdcard/ui.xml"))

    def screenshot(self, out_path: str) -> Path:
        self._run("shell", "screencap", "-p", "/sdcard/screen.png")
        cmd = [self.adb_path, *(["-s", self.serial] if self.serial else []),
               "exec-out", "cat", "/sdcard/screen.png"]
        data = subprocess.run(cmd, capture_output=True, timeout=60)
        path = Path(out_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.stdout)
        return path

    def tap(self, x: int, y: int) -> None:
        self._run("shell", "input", "tap", str(x), str(y))

    def type_text(self, text: str) -> None:
        self._run("shell", "input", "text", text.replace(" ", "%s"))

    def back(self) -> None:
        self._run("shell", "input", "keyevent", "4")

    def current_activity(self) -> str:
        out = self._run("shell", "dumpsys", "window", "windows")
        for line in out.splitlines():
            if "mCurrentFocus" in line:
                return line.strip()
        return ""


def parse_uiautomator_xml(xml_text: str) -> dict:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise AdbError(f"не разобрать UI-дамп: {exc}") from exc

    if root.tag == "hierarchy":
        found = root.find("node")
        if found is not None:
            root = found

    def walk(node) -> dict:
        return {
            "class": node.get("class", ""),
            "text": node.get("text", ""),
            "resource_id": node.get("resource-id", ""),
            "content_desc": node.get("content-desc", ""),
            "clickable": node.get("clickable", "false") == "true",
            "bounds": node.get("bounds", ""),
            "children": [walk(child) for child in list(node)],
        }

    return walk(root)


def ui_tree_to_flat(tree: dict) -> list[dict]:
    flat, stack = [], [tree]
    while stack:
        node = stack.pop()
        flat.append({k: node[k] for k in
                     ("class", "text", "resource_id", "content_desc", "clickable", "bounds")})
        stack.extend(reversed(node["children"]))
    return flat


def screen_markdown(tree: dict, screen_name: str) -> str:
    flat = ui_tree_to_flat(tree)
    lines = [f"# Экран {screen_name}", "", "## Интерактивные узлы", ""]
    for node in flat:
        if not (node["clickable"] or node["resource_id"] or node["text"]):
            continue
        tag = node["resource_id"] or "—"
        label = node["text"] or node["content_desc"] or "—"
        lines.append(f"- `{tag}` — «{label}» ({node['class']}) {node['bounds']}")
    lines += ["", "## Дерево", ""]

    def walk_children(node: dict, depth: int) -> None:
        lines.append("  " * depth + f"{node['class']} {node['resource_id']}".strip())
        for child in node["children"]:
            walk_children(child, depth + 1)

    walk_children(tree, 0)
    return "\n".join(lines) + "\n"
