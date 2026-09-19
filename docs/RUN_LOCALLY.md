# Локальный запуск сайта

Как поднять весь стек на этой машине и увидеть содержимое архива в браузере.

```text
http://localhost:3000        MURA-app      (Next.js 15, Turbopack)
        │  server-side proxy, bearer добавляется на сервере
        ▼
http://127.0.0.1:8001        Mura Core     (FastAPI)
        │
        ▼
127.0.0.1:5432               PostgreSQL 18  база mura_leases_test
```

## Требования

- Node 20+ и Python 3.11+ — на машине уже стоят.
- PostgreSQL 18, запущенный как служба `postgresql-x64-18` на `127.0.0.1:5432`.
  Пароль роли `mura_test` берётся из `%APPDATA%\postgresql\pgpass.conf`; в
  репозиторий он не попадает. Поэтому в `DATABASE_URL` пароля нет — и это работает.
- OpenAI-совместимый ASR-хост или Kaggle GPU **не** нужны: они нужны только
  worker-процессу, а он локально не поднимается (см. «Что работает»).

## Что уже подготовлено

| Что | Где |
| --- | --- |
| Зависимости фронтенда | `MURA-app/node_modules` |
| Зависимости Core | `Mura_project/.venv` (Python 3.13.1) |
| Конфигурация фронтенда | `MURA-app/.env.local` |
| Конфигурация Core | `Mura_project/.env` |
| Схема БД | приведена к `alembic head` (`20260913_0011`) |

Оба env-файла в `.gitignore` и содержат только локальные значения. Секреты в
`Mura_project/.env` сгенерированы здесь же через `secrets.token_urlsafe(48)`.

## Запуск

Нужны два терминала: Core не работает внутри Next.js, и serving запроса не
запускает обработку.

**Терминал 1 — Core:**

```powershell
cd d:\Mura_production\Mura_project
.\.venv\Scripts\python.exe -m uvicorn apps.api.main:app --host 127.0.0.1 --port 8001
```

**Терминал 2 — сайт:**

```powershell
cd d:\Mura_production\MURA-app
npm run dev
```

Проверка, что Core жив:

```powershell
Invoke-WebRequest http://127.0.0.1:8001/health -UseBasicParsing | Select-Object -ExpandProperty Content
# {"status":"ok","service":"mura-core"}
```

## Вход

Провайдер Clerk здесь не настроен — вместо него работает локальный
issuance-провайдер (`MURA_DEV_AUTH=true`). Core проверяет его токены по-настоящему:
RS256, подпись по JWKS с `/api/dev-auth/jwks.json`, а также `iss`, `aud`, `exp`,
`nbf` и `sub`. Отличается только то, *кто выдаёт* токен.

1. Откройте `http://localhost:3000`.
2. `/sign-in` → введите email **`dev@mura.local`** → войдите.

Пароль не запрашивается и не проверяется: это локальный issuer для одного
разработчика, и он не может включиться вне `next dev` — нужны сразу три условия:
`MURA_DEV_AUTH=true`, не production-сборка, не развёртывание на Vercel.

Email важен: пользователь Core заводится по паре `issuer + subject`, поэтому архив,
наполненный под `dev@mura.local`, виден только под ним. Под другим email Core
создаст второго пользователя, у него не будет семьи, и появится экран «создайте
семью». Это штатное поведение, а не ошибка.

## Наполнение архива

Записать голос локально нельзя (нет worker-а), поэтому архив наполняется
seed-скриптом. Он идёт через `ArchiveRepository.persist_pipeline_result` — тот же
путь, которым пишет worker, а не через прямые INSERT: то, что вы увидите,
произведено канонической персистенцией.

```powershell
cd d:\Mura_production\Mura_project
.\.venv\Scripts\python.exe scripts\seed_dev_family.py <family_id>
.\.venv\Scripts\python.exe scripts\seed_dev_family.py <family_id> --variant bread
```

`family_id` виден в адресной строке `/tree?family=…` или в ответе
`POST /v1/families`. Скрипт отказывается работать, если имя базы не содержит `test`.

Каждый прогон добавляет одну запись: людей, историю, связи и открытый вопрос.
После двух прогонов `/home` показывает 5 человек, 2 истории, 2 записи и 1 вопрос
на ревью.

## Что работает и что нет

| Экран | Локально |
| --- | --- |
| `/` | работает — лендинг, выбор языка |
| `/home` | работает — счётчики архива, люди, последние истории |
| `/tree` | работает — граф строится из `people` + `relationships` |
| `/stories`, `/story/[id]` | работают |
| `/person/[id]` | работает |
| `/review` | работает — открытые вопросы из seed |
| `/settings` | работает |
| `/ask` | демонстрационный ответ, помечен бейджем: backend для Ask не существует |
| `/record` → `/processing` | **не завершится** — см. ниже |

**Запись и обработка.** Загрузка аудио в Core работает, задание встаёт в очередь
`processing_jobs` — и остаётся там: worker не запущен, а без него нет ни ASR, ни
DeepSeek. Чтобы пройти путь целиком, нужны `WHISPER_API_KEY` (или Kaggle GPU) и
настоящий `DEEPSEEK_API_KEY` в `Mura_project/.env`, а затем отдельный процесс:

```powershell
cd d:\Mura_production\Mura_project
.\.venv\Scripts\mura-worker.exe
```

(entrypoint `apps.worker.main:main`; отдельного модуля `mura.worker` нет, поэтому
`python -m mura.worker` не сработает.)

Это уже не «посмотреть содержимое», а прогон ML-конвейера, и он упирается во
внешние сервисы и их оплату.

## Остановка

Закройте окна терминалов, из которых запускали `npm run dev` и `uvicorn`, либо:

```powershell
Get-CimInstance Win32_Process -Filter "Name='node.exe' OR Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*Mura_production*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

## Если что-то не отвечает

| Симптом | Причина |
| --- | --- |
| Семейные экраны: «вход ещё не настроен» | не задан `MURA_DEV_AUTH=true` либо `next dev` не перезапущен после правки `.env.local` |
| `401 invalid_token` от Core | `DEV_AUTH_ISSUER` / `DEV_AUTH_AUDIENCE` не совпадают с `AUTH_ISSUER` / `AUTH_AUDIENCE` в `Mura_project/.env` |
| «Сервис временно недоступен» (503) | Core не запущен или `MURA_API_URL` указывает не туда |
| Токены перестали приниматься после чистки | удалён `.dev-auth/issuer-key.pem`; Core перечитает JWKS в течение `AUTH_JWKS_CACHE_SECONDS` |
| `alembic` не подключается к базе | служба PostgreSQL остановлена или запись в `pgpass.conf` потеряна |
| В `.env.local` пусто, экраны просят вход | `next dev` читает env-файлы только при старте — нужен перезапуск |

Полезные адреса при `EXPOSE_API_DOCS=true`:
`http://127.0.0.1:8001/docs` — OpenAPI Core, `http://127.0.0.1:8001/v1/capabilities`
без токена отвечает `401` (это ожидаемо).

## Границы

Локальный issuer — это **не** способ обойти авторизацию. Он не включается ни в
production-сборке, ни на Vercel, не подменяет собой отсутствующую настройку Clerk и
не даёт Core служебный ключ: `server-session.ts` по-прежнему отказывает, если
пользовательской сессии нет, а прокси отклоняет любой `Authorization` от браузера.
Членство в семье читается из PostgreSQL на каждом запросе, поэтому BOLA-поведение
(`404` вместо `403` для чужой семьи) локально такое же, как в production.

`mura_leases_test` — одноразовая база для тестов и локального просмотра. В ней нет
и не должно быть реального семейного архива: seed-скрипт для этого и требует, чтобы
имя базы содержало `test`.