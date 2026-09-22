"""MCP-проверка (день 16): подключиться к MCP-серверам и вывести список
доступных инструментов.

Проверяет два пункта:
  1. соединение устанавливается (initialize + tools/list ответили);
  2. список инструментов корректно возвращается (name + description).

Запуск из корня репозитория:
    python scripts/check_mcp_tools.py              # все серверы реестра
    python scripts/check_mcp_tools.py Firecrawl    # только Firecrawl
    python scripts/check_mcp_tools.py --list       # реестр без подключения

Секреты — из .env в корне репозитория; плейсхолдеры {VAR} в command/env
расширяются из окружения.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(REPO, ".env"))

from studio.backend.memory import MemoryStore  # noqa: E402
from studio.backend.mcp import MCPError, MCPRegistry  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    list_only = "--list" in sys.argv

    store = MemoryStore(os.path.join(REPO, "studio", "data"))
    reg = MCPRegistry(store)
    servers = reg.servers()
    try:
        if not servers:
            print("Реестр пуст")
            return 1

        if list_only:
            for s in servers:
                print(f"{s['name']} | {s['type']} | enabled={s['enabled']} "
                      f"| {s['command'] or s['url']}")
            return 0

        failed = 0
        for s in servers:
            if args and s["name"] not in args:
                continue
            print(f"→ {s['name']} ({s['type']}: {s['command'] or s['url']})")
            try:
                view = reg.connect(s["id"])
            except MCPError as e:
                print(f"  ERROR: {e}")
                failed += 1
                continue
            if view.get("status") != "connected":
                print(f"  ERROR: {view.get('error')}")
                failed += 1
                continue
            print(f"  OK: connected, tools_count={view.get('tools_count')}")
            tools = [t for t in reg.tools() if t["server"] == s["id"]]
            if not tools:
                print("  (инструментов нет)")
            for t in tools:
                desc = (t.get("description") or "").replace("\n", " ")
                print(f"  - {t['name']}: {desc[:100]}")
        # имена, которые не найдены в реестре
        known = {s["name"] for s in servers}
        for w in args:
            if w not in known:
                print(f"!! сервер «{w}» нет в реестре")
                failed += 1
        return 1 if failed else 0
    finally:
        reg.close_all()


if __name__ == "__main__":
    sys.exit(main())
