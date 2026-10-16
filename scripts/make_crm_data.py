#!/usr/bin/env python3
"""Генератор CRM-данных для ассистента поддержки (дни 30–34).

Создаёт `data/crm/users.json` и `data/crm/tickets.json` —
детерминированный источник тикетов и пользователей, по которому
модуль поддержки (`assistant.TicketStore`) персонализирует ответы.

Детерминизм: данные заданы статически (никакого random без seed),
повторный запуск даёт байт-в-байт тот же результат — иначе e2e и
тесты были бы невоспроизводимы.

Запуск:
    .venv/bin/python scripts/make_crm_data.py
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CRM_DIR = os.path.join(REPO_ROOT, "data", "crm")

# Пользователи: план (free/pro/team/enterprise), роль, статус оплаты,
# дата регистрации, канал, число обращений. Поля выбраны так, чтобы
# персонализация была ОСМЫСЛЕННОЙ: план и оплата меняют тон ответа,
# роль — глубину технических деталей, канал — формат.
USERS = [
    {
        "id": "u-1001",
        "name": "Игорь Владимирович",
        "email": "igor@example.com",
        "plan": "team",
        "role": "admin",
        "payment_status": "ok",
        "registered_at": "2026-01-14",
        "channel": "web",
        "locale": "ru",
        "tickets_count": 2,
    },
    {
        "id": "u-1002",
        "name": "Мария Соколова",
        "email": "maria.s@example.com",
        "plan": "pro",
        "role": "developer",
        "payment_status": "ok",
        "registered_at": "2026-02-03",
        "channel": "telegram",
        "locale": "ru",
        "tickets_count": 1,
    },
    {
        "id": "u-1003",
        "name": "Alex Petrov",
        "email": "alex@example.com",
        "plan": "free",
        "role": "user",
        "payment_status": "failed",
        "registered_at": "2026-03-21",
        "channel": "email",
        "locale": "en",
        "tickets_count": 3,
    },
    {
        "id": "u-1004",
        "name": "Ольга Ким",
        "email": "olga.kim@example.com",
        "plan": "enterprise",
        "role": "admin",
        "payment_status": "pending",
        "registered_at": "2025-11-08",
        "channel": "web",
        "locale": "ru",
        "tickets_count": 1,
    },
]

# Тикеты: темы покрывают три сценария из задания — авторизация, оплата,
# конфигурация. Поле `resolution` пустое у открытых (ассистент должен
# ответить сам), заполнено у закрытых (историческая справка).
TICKETS = [
    {
        "id": "T-5001",
        "user_id": "u-1001",
        "subject": "Не приходит письмо сброса пароля",
        "body": ("Запрашиваю сброс пароля третий раз, письмо не приходит. "
                 "Проверял спам — пусто. Домен корпоративный."),
        "topic": "auth",
        "status": "open",
        "priority": "high",
        "created_at": "2026-04-02T09:14:00Z",
        "tags": ["auth", "email", "password-reset"],
        "resolution": None,
    },
    {
        "id": "T-5002",
        "user_id": "u-1002",
        "subject": "Как подключить локальную модель Ollama?",
        "body": ("Хочу выключить внешний API и работать на своей машине. "
                 "Какая модель нужна для поиска по документам?"),
        "topic": "configuration",
        "status": "open",
        "priority": "medium",
        "created_at": "2026-04-03T11:02:00Z",
        "tags": ["ollama", "local", "embedder", "rag"],
        "resolution": None,
    },
    {
        "id": "T-5003",
        "user_id": "u-1003",
        "subject": "Оплата не прошла, доступ закрыт",
        "body": ("Карта отклонена, но деньги списались. Доступ к проекту "
                 "пропал. Прошу вернуть доступ."),
        "topic": "billing",
        "status": "open",
        "priority": "high",
        "created_at": "2026-04-03T16:40:00Z",
        "tags": ["billing", "payment-failed", "access"],
        "resolution": None,
    },
    {
        "id": "T-5004",
        "user_id": "u-1004",
        "subject": "Лимит контекста в шлюзе: как увеличить?",
        "body": ("Развернули шлюз для команды, упираемся в max context "
                 "при длинных диалогах. Как настроить лимиты на клиента?"),
        "topic": "configuration",
        "status": "open",
        "priority": "medium",
        "created_at": "2026-04-04T08:20:00Z",
        "tags": ["gateway", "max-context", "limits", "lan"],
        "resolution": None,
    },
    {
        "id": "T-5005",
        "user_id": "u-1002",
        "subject": "Реранкер не находит нужный документ",
        "body": ("При включённом реранкере ответы стали хуже, чем без "
                 "него. Порог релевантности не помогает."),
        "topic": "configuration",
        "status": "closed",
        "priority": "low",
        "created_at": "2026-03-28T13:05:00Z",
        "tags": ["rag", "reranker", "min-score"],
        "resolution": ("Порог min_score применяется относительно лучшего "
                       "результата при выключенном реранкере; при "
                       "включённом — абсолютный. Снизили min_score до "
                       "0.3, проблема ушла."),
    },
    {
        "id": "T-5006",
        "user_id": "u-1001",
        "subject": "MCP-сервер не подключается по stdio",
        "body": ("Прописал свой сервер в mcp_servers.json, статус "
                 "остаётся «не подключён»."),
        "topic": "configuration",
        "status": "closed",
        "priority": "medium",
        "created_at": "2026-03-30T10:00:00Z",
        "tags": ["mcp", "stdio", "json-rpc"],
        "resolution": ("Команда сервера указывалась относительным путём. "
                       "Нужен абсолютный либо доступный в PATH; "
                       "проверено через scripts/check_mcp_tools.py."),
    },
]


def main() -> int:
    os.makedirs(CRM_DIR, exist_ok=True)
    users = {"version": 1, "users": USERS}
    tickets = {"version": 1, "tickets": TICKETS}
    for name, payload in (("users.json", users),
                          ("tickets.json", tickets)):
        path = os.path.join(CRM_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print(f"записано: {os.path.relpath(path, REPO_ROOT)}")
    print(f"пользователей: {len(USERS)}, тикетов: {len(TICKETS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
