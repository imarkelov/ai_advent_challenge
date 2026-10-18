# Паттерны автотестов проекта

## Screen-объекты

Экраны описываются классами `ComposeScreen<...>` из Kakao в пакете
`presentation/main/screens/`. Пример: `AddNewAddressScreen` с полями
`toolbarTitleText`, `acceptButton`, вложенным классом `TextField`.

## Матчеры

- `withTestTag(Constants.X, true)` — поиск по test-tag.
- `tagged(Constants.X)` — короткая форма того же.
- `withText("...", exactly = true, unmergedTree = true)` — по видимому тексту.

## Константы тегов

Теги объявлены в объектах `*UIConstants` соответствующих модулей, например
`AddressAddUIConstants.AcceptButton` или `AddressSearchUIConstants.TextField`.
Тесты ссылаются на константы, а не на строковые литералы.

## Шаги и отчёты

Kaspresso: `run { step("...") { ... } }`. Классы тестов помечаются
Allure-аннотациями, шаги попадают в отчёт.

## Фикстуры

JSON-моки в `androidTest/assets/responses/`, тестовые данные в
`presentation/main/data/`.
