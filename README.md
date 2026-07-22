# PostIt Agent

Локальный MVP для риелтора: одна карточка объекта → фото и голос → AI extraction → подтверждение полей → тексты под площадки → feed/assisted export → статусы публикаций.

README актуализирован под фактическое состояние проекта на Wednesday, July 22, 2026.

## Что реально работает

- `FastAPI` backend с локальной `sqlite`-персистентностью.
- Регистрация, вход, JWT и защищённые endpoint'ы.
- Карточка объекта, профиль риелтора и список объектов.
- Загрузка фото, фото-анализ, выбор обложки, изменение порядка, удаление media.
- Загрузка аудиофайла или mock-транскрипта.
- STT-провайдеры:
  - `mock` по умолчанию;
  - `whisper.cpp` при настроенном бинарнике и модели.
- AI extraction с сохранением `evidence` и обязательным подтверждением критичных AI-полей.
- Генерация базового и платформенных текстов через `mock` или `ollama`.
- Валидаторы площадок с честным блокированием неподтверждённых AI-полей.
- Фиды для `CIAN` и `Yandex Realty`.
- Assisted export для всех площадок: `ZIP`, `TXT`, `DOCX`, `JSON`, `HTML`.
- `PublicationJob` со статусами `needs_review`, `waiting_for_platform`, `needs_action`, `published`, `deactivated`.
- Web-shell на vanilla JS, пригодный для локального mock-flow.
- Health/readiness endpoint'ы: `/health`, `/ready`.

## Чего здесь пока нет

- PostgreSQL / SQLAlchemy / Alembic как основной persistence-слой.
- Redis и отдельный worker-процесс с очередью задач.
- Подтверждённые прямые API-интеграции публикации на площадки.
- Автоматическое подтверждение статуса публикации со стороны площадок.

То есть текущая версия — честный локальный MVP-вертикальный срез, а не завершённая production-архитектура.

## Архитектура

```text
src/postit_agent/
  main.py         FastAPI app, middleware, error envelope, versioned routes
  services.py     Основная orchestration-логика MVP
  repository.py   SQLite storage c JSON blobs
  ai.py           Mock/Ollama extraction + copy, mock/whisper.cpp STT
  adapters.py     Feed/export adapters per platform
  models.py       Pydantic domain models
  static/         Web-shell (HTML/CSS/vanilla JS)
```

Существующая архитектура сохранена, но усилена evidence-flow, media-операциями и versioned API вместо переписывания проекта с нуля.

## Локальный запуск

```bash
cp .env.example .env
make install
make dev
```

После старта доступны:

- `http://localhost:8000/`
- `http://localhost:8000/docs`
- `http://localhost:8000/health`
- `http://localhost:8000/ready`

## Docker

```bash
cp .env.example .env
docker compose up --build
```

По умолчанию приложение запускается в `mock`-режиме и не требует внешней LLM.

## Основные переменные окружения

Смотри `.env.example`. Наиболее важные:

```env
POSTIT_ENVIRONMENT=development
POSTIT_DEBUG=true
POSTIT_HOST=0.0.0.0
POSTIT_PORT=8000

POSTIT_SECRET_KEY=change-me-in-production
POSTIT_ACCESS_TOKEN_EXPIRE_MINUTES=10080
POSTIT_PUBLIC_BASE_URL=http://localhost:8000
POSTIT_CORS_ORIGINS=http://localhost:8000,http://127.0.0.1:8000

POSTIT_AI_PROVIDER=mock
POSTIT_OLLAMA_BASE_URL=http://localhost:11434
POSTIT_OLLAMA_MODEL=qwen2.5:7b

POSTIT_STT_PROVIDER=mock
POSTIT_WHISPER_CPP_BINARY=
POSTIT_WHISPER_CPP_MODEL_PATH=

POSTIT_MAX_UPLOAD_SIZE_MB=30
POSTIT_ALLOWED_AUDIO_FORMATS=wav,mp3,m4a,ogg,webm,txt
POSTIT_ALLOWED_IMAGE_FORMATS=jpg,jpeg,png,webp
```

## Пользовательский сценарий MVP

1. Зарегистрироваться или войти.
2. Заполнить профиль риелтора.
3. Создать карточку объекта.
4. Загрузить фотографии.
5. Загрузить аудио или вставить mock-транскрипт.
6. Нажать `Извлечь поля`.
7. Подтвердить или отклонить AI-подсказки в блоке `AI Evidence`.
8. Сгенерировать тексты.
9. Проверить валидацию.
10. Собрать публикации и assisted export.

## API

Поддерживаются совместимые старые маршруты и versioned route-set `api/v1`.

Ключевые endpoint'ы:

- `GET /health`
- `GET /ready`
- `POST /api/v1/auth/register`
- `POST /api/v1/auth/login`
- `GET /api/v1/auth/me`
- `POST /api/v1/properties`
- `GET /api/v1/properties`
- `GET /api/v1/properties/{id}`
- `PATCH /api/v1/properties/{id}`
- `DELETE /api/v1/properties/{id}`
- `POST /api/v1/properties/{id}/media`
- `GET /api/v1/properties/{id}/media`
- `PATCH /api/v1/properties/{id}/media/order`
- `PATCH /api/v1/properties/{id}/media/{media_id}`
- `DELETE /api/v1/properties/{id}/media/{media_id}`
- `POST /api/v1/properties/{id}/transcriptions`
- `POST /api/v1/properties/{id}/extract`
- `POST /api/v1/properties/{id}/analyze-photos`
- `POST /api/v1/properties/{id}/generate-copy`
- `GET /api/v1/properties/{id}/evidence`
- `POST /api/v1/properties/{id}/evidence/{evidence_id}/confirm`
- `POST /api/v1/properties/{id}/evidence/{evidence_id}/reject`
- `POST /api/v1/properties/{id}/validate`
- `POST /api/v1/properties/{id}/publications`
- `GET /api/v1/properties/{id}/publications`
- `GET /api/v1/publications/{id}`
- `POST /api/v1/publications/{id}/mark-published`
- `POST /api/v1/publications/{id}/deactivate`
- `GET /feeds/{user_id}/{platform}.xml`
- `GET /exports/{export_id}.zip`
- `GET /api/v1/exports/{export_id}`

Все ошибки возвращаются в едином envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "...",
    "details": []
  },
  "request_id": "..."
}
```

## Ограничения интеграций

- `CIAN` — feed / manual import через XML.
- `Yandex Realty` — feed / manual import через YRL.
- `Avito` — `user_assisted`, XML и пакет для ручной публикации.
- `Youla` — `url_feed`, если аккаунт так настроен; иначе manual/user-assisted.
- `Domclick` — только assisted/manual сценарий, без имитации прямой публикации.

Ни одна площадка не помечается как «автоматически опубликована», пока пользователь явно не подтвердит результат или не сохранит внешний URL объявления.

## Тесты и smoke

```bash
make test
```

Локально проверены:

- unit-тесты mock AI/adapters;
- service-level smoke сценарий полного mock-flow;
- media reorder / cover update;
- pending evidence блокирует публикацию до подтверждения или отклонения.

Подробности — в `docs/TESTING.md`.
