#!/usr/bin/env bash
# День 32 — локальный запуск AI-ревью PR (тот же пайплайн, что в GitHub Action).
#
# Три команды:
#   scripts/day32_pr_review.sh demo [--no-llm]   создать демо-репозиторий и
#                                                прогнать пайплайн на его PR
#   scripts/day32_pr_review.sh repo [--base REF]  ревью изменений этого
#                                                репозитория против ветки
#   scripts/day32_pr_review.sh <owner/repo#N>     ревью публичного PR из GitHub
#
# Модель: из окружения LLM_MODEL / LLM_BASE_URL / LLM_API_KEY (лучше — в
# .env, он подхватывается), иначе из конфига Студии (Ollama). Без модели
# отчёт строится детерминированно — пайплайн не падает.
#
# Примеры:
#   scripts/day32_pr_review.sh demo
#   LLM_MODEL=qwen3:8b LLM_BACKEND=ollama scripts/day32_pr_review.sh repo
set -euo pipefail
cd "$(dirname "$0")/.." || exit 1

PY="${PY:-.venv/bin/python}"
[ -x "$PY" ] || { echo "Нет $PY — создайте venv: python3 -m venv .venv && .venv/bin/pip install -r studio/backend/requirements.txt" >&2; exit 1; }

# .env проекта → окружение (LLM_*, OLLAMA_*), не перетирая уже заданные.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

cmd="${1:-demo}"
shift || true
OUT_DIR="${OUT_DIR:-./.day32_review}"
mkdir -p "$OUT_DIR"

run_pipeline() {   # <repo> <diff|base|github> [extra args...]
  local repo="$1" src="$2"
  shift 2
  # --no-llm — то, что получит `pr-review run`; прочие флаги — как есть.
  # (bash 3.2 + `set -u`: пустые массивы раскрываются как unbound,
  # поэтому без "${arr[@]}" передаём через подстановку строки.)
  local run_args=""
  case "$src" in
    git:*)  run_args="--base ${src#git:}" ;;
    file:*) run_args="--diff ${src#file:}" ;;
    gh:*)   run_args="--github-pr ${src#gh:}" ;;
    *) echo "Неизвестный источник diff: $src" >&2; return 2 ;;
  esac
  local extra=""
  for a in "$@"; do extra="$extra $a"; done

  # shellcheck disable=SC2086
  "$PY" studio/backend/cli.py --repo "$repo" pr-review check
  "$PY" studio/backend/cli.py --repo "$repo" index
  # shellcheck disable=SC2086
  "$PY" studio/backend/cli.py --repo "$repo" pr-review run $run_args \
      --annotations \
      --step-summary "$OUT_DIR/summary.md" \
      --out "$OUT_DIR/review.md" --json-out "$OUT_DIR/review.json" \
      $extra
  echo
  echo "Отчёт: $OUT_DIR/review.md · данные: $OUT_DIR/review.json"
}

case "$cmd" in
  demo)
    WORK="${WORK_DIR:-$OUT_DIR/demo-repo}"
    rm -rf "$WORK"
    echo "Демо-репозиторий: $WORK"
    "$PY" scripts/day32_demo_repo.py "$WORK"
    run_pipeline "$WORK" git:main "$@"
    ;;
  repo)
    base="${BASE:-origin/master}"
    rest=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --base) base="$2"; shift 2 ;;
        *) rest="$rest $1"; shift ;;
      esac
    done
    if ! git rev-parse --verify --quiet "$base" >/dev/null 2>&1; then
      echo "Ветка $base не найдена: сделайте git fetch или укажите --base REF" >&2
      exit 1
    fi
    # shellcheck disable=SC2086
    run_pipeline "." "git:$base" $rest
    ;;
  *'#'*)
    run_pipeline "." "gh:$cmd" "$@"
    ;;
  *)
    echo "Использование: $0 [demo [--no-llm] | repo [--base REF] | owner/repo#N]" >&2
    exit 2
    ;;
esac
