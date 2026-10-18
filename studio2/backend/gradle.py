"""Запуск Gradle-задач тестируемого Android-проекта."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

FAIL_RE = re.compile(r"^\s*([\w\.]+) > ([\w\[\]\-]+) FAILED", re.MULTILINE)


class GradleError(RuntimeError):
    pass


def parse_build_result(log: str) -> dict:
    failures = [
        {"class": m.group(1), "test": m.group(1).split(".")[-1], "method": m.group(2)}
        for m in FAIL_RE.finditer(log)
    ]
    match = re.search(r"BUILD (?:SUCCESSFUL|FAILED) in ([\w ]+)", log)
    return {
        "success": "BUILD SUCCESSFUL" in log and not failures,
        "failures": failures,
        "duration": match.group(1).strip() if match else "",
    }


class GradleRunner:
    def __init__(self, repo_path: str, java_home: str | None = None,
                 test_module: str = ":android:app"):
        self.repo_path = Path(repo_path)
        self.java_home = java_home or os.getenv("JAVA_HOME")
        self.test_module = test_module

    def _gradlew(self) -> Path:
        for name in ("gradlew.bat", "gradlew"):
            candidate = self.repo_path / name
            if candidate.exists():
                return candidate
        raise GradleError(f"в {self.repo_path} нет gradlew")

    def mode(self) -> str:
        try:
            self._gradlew()
        except GradleError:
            return "fake"
        return "live"

    def build_command(self, *tasks: str) -> list[str]:
        return [str(self._gradlew()), *tasks]

    def connected_test_command(self, class_name: str) -> list[str]:
        return [
            str(self._gradlew()),
            f"{self.test_module}:connectedDebugAndroidTest",
            f"-Pandroid.testInstrumentationRunnerArguments.class={class_name}",
        ]

    def _run(self, args: list[str], log_path: Path, timeout: int) -> dict:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        if self.java_home:
            env["JAVA_HOME"] = self.java_home
        try:
            result = subprocess.run(args, cwd=str(self.repo_path), capture_output=True,
                                    text=True, encoding="utf-8", errors="replace",
                                    timeout=timeout, env=env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise GradleError(f"Gradle не запустился: {exc}") from exc
        log = (result.stdout or "") + "\n" + (result.stderr or "")
        log_path.write_text(log, encoding="utf-8")
        parsed = parse_build_result(log)
        parsed.update(returncode=result.returncode, log_path=str(log_path))
        return parsed

    def assemble_debug(self, log_path: Path) -> dict:
        return self._run([str(self._gradlew()), f"{self.test_module}:assembleDebug"],
                         log_path, timeout=1500)

    def connected_test(self, class_name: str, log_path: Path) -> dict:
        return self._run(self.connected_test_command(class_name), log_path, timeout=900)
