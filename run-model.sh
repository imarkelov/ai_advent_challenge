#!/usr/bin/env bash
# Переключение между MLX-моделями по очереди: один процесс в памяти за раз.
# Использование:
#   ./run-model.sh list          — список моделей
#   ./run-model.sh chat <key>    — интерактивный чат
#   ./run-model.sh gen  <key> "промпт"
#   ./run-model.sh serve <key> [port]
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/.venv-mlx/bin/python"
# Модели живут в единой папке ~/AiModels/mlx (см. ~/AiModels/README.md)
export HF_HOME="${HF_HOME:-$HOME/AiModels/mlx}"
[ -x "$PY" ] || { echo "Нет venv: $PY. Создайте: python3 -m venv .venv-mlx && .venv-mlx/bin/pip install mlx-lm" >&2; exit 1; }

# key|repo|описание
MODELS=(
  "abl27|mlx-community/Huihui-Qwen3.5-27B-Claude-4.6-Opus-abliterated-4bit|Qwen3.5-27B abliterated 4bit (16 GB)"
  "abl35moe|mlx-community/Huihui-Qwen3.5-35B-A3B-Claude-4.6-Opus-abliterated-4bit|Qwen3.5-35B-A3B MoE abliterated (20 GB)"
  "base8|mlx-community/Qwen3-8B-4bit|Qwen3-8B обычная 4bit (baseline, 4.6 GB)"
  "abl14|mlx-community/Josiefied-Qwen3-14B-abliterated-v3-4bit|Qwen3-14B abliterated 4bit (8 GB)"
  "abl4|mlx-community/Josiefied-Qwen3-4B-abliterated-v1-4bit|Qwen3-4B abliterated 4bit (2.3 GB)"
)

lookup() {
  local k="$1"
  for e in "${MODELS[@]}"; do
    IFS='|' read -r key repo desc <<<"$e"
    [ "$key" = "$k" ] && { echo "$repo"; return 0; }
  done
  echo "Неизвестный ключ: $k" >&2; return 1
}

cmd="${1:-list}"
case "$cmd" in
  list)
    printf '%-9s %-12s %s\n' "KEY" "SIZE" "MODEL"
    for e in "${MODELS[@]}"; do
      IFS='|' read -r key repo desc <<<"$e"
      mark=" "
      [ -d "${HF_HOME}/hub/models--${repo//\//--}" ] && mark="*"
      printf '%s%-8s %-12s %s\n' "$mark" "$key" "$(echo "$desc" | grep -o '([^)]*)')" "$repo"
    done
    echo
    echo "* = уже скачана. Память занимает только запущенная модель."
    ;;
  chat|gen|serve)
    key="${2:-}"; [ -n "$key" ] || { echo "Укажите ключ модели." >&2; exit 1; }
    repo="$(lookup "$key")"
    case "$cmd" in
      chat)  exec "$PY" -m mlx_lm.chat --model "$repo" ;;
      gen)   shift 2; exec "$PY" -m mlx_lm.generate --model "$repo" --prompt "${1:-Привет}" --max-tokens "${2:-400}" ;;
      serve) port="${3:-8080}"; echo "OpenAI-совместимый API: http://127.0.0.1:$port/v1"; exec "$PY" -m mlx_lm.server --model "$repo" --port "$port" ;;
    esac
    ;;
  *) echo "Команды: list | chat <key> | gen <key> \"промпт\" | serve <key> [port]" >&2; exit 1 ;;
esac
