# Design

<!-- impeccable:design-schema 1 -->

## Mode

Operate — плотная форма, журнал справа, быстрые повторные запуски.

## Layout

- Верхняя панель: заголовок, тема, статус, кнопки запуска.
- Слева: одна прокручиваемая колонка — все секции настроек подряд.
- Справа: журнал (monospace).

## Color

- Light: white / gray-3 borders / blue accent `#228be6`.
- Dark: `#25262b` navbar, `#373a40` borders, active link blue-light wash.
- Theme toggle persists in `localStorage` (`poster-ui-theme`).

## Type

Operate scale (tokens in `panel.html`):

| Role | Token | Size |
|------|--------|------|
| Display (h1) | `--type-display-*` | 26px / 700 |
| Title (section) | `--type-title-*` | 20px / 600 |
| Body / nav | `--type-body-*` | 14px / 400, leading 1.5 (1.55 dark) |
| UI buttons | `--type-ui-*` | 13px / 500 |
| Labels, hints | `--type-label-*` | 12px / 500 |
| Meta, footer | `--type-meta-*` | 11px caps |
| Log | `--type-code-*` | 13px mono, tabular nums |

Family: `--font-sans` (Segoe UI), `--font-mono` (Consolas). Prose max `--measure-prose` 65ch. Headings `text-wrap: balance`; body/hints `pretty`.

## Shape

Radius 4px (Mantine sm). Без gradient hero. Иконки навигации — inline SVG 20px, stroke 1.75.

## Components

- `.link` nav items with `[data-active]` state.
- `.action` buttons; primary = blue fill.
- Inputs full-width in settings column.
