# Архитектура тестируемого проекта

## Назначение

Карта проекта для автотестов: модули, экраны, навигация, ключевые классы.
Файл ведёт агент-исследователь и дополняет человек.

## Модули

- `android/app` — точка входа Android-приложения, `namespace` и
  `applicationId` равны `ru.leroymerlin.mobile`.
- `android/presentation` — экраны и навигация.
- `common/*` — feature-модули: address, cart, catalog, checkout, profile,
  search, promo, loyalty и другие.
- `ios/*` — iOS-часть, в Android-пайплайне не тестируется.

## Тесты

- Android UI-тесты лежат в
  `android/app/src/androidTest/java/ru/leroymerlin/mobile/presentation/`.
- Стек: Kaspresso, Kakao, Compose UI Test, Espresso, Allure.
- Instrumentation runner: `io.qameta.allure.android.runners.AllureAndroidJUnitRunner`.
- Моки сетевых ответов: `androidTest/assets/responses/`.

## Навигация

Обновляется по мере изучения проекта.
