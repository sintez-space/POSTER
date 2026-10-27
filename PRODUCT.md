# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Static `panel.html` + Python `panel.py` (localhost HTTP). No bundler.

## Users

Один оператор на своём ПК: настраивает Chrome CDP, папку постов, сети (VK, TikTok, …) и запускает автозаполнение форм публикации.

## Product Purpose

Локальная панель POSTER: сохранить конфиг, запустить `poster.py`, смотреть журнал. Успех — отложенный пост ушёл без ручного копирования текста в браузер.

## Positioning

Привязка к своему Chrome-профилю и CDP-порту; не облачный SaaS и не headless без логина.

## Accessibility & Inclusion

Рабочий инструмент на десктопе; контраст и читаемость журнала важнее декора.

## Principles

- Одно главное действие на экран: «Заполнить формы».
- Ошибки видны в статусе и журнале.
- Только localhost, без секретов в UI.
