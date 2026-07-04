# Запуск и тестирование

## 1. Локальный запуск без Docker

```bash
cp .env.example .env
make install
make dev
```

После старта открой `http://localhost:8000/`.

По умолчанию backend работает в `mock`-режиме и не требует локальной LLM.

## 2. Локальный запуск с Ollama

1. Подними Ollama на своей машине или в Docker.
2. Загрузи модель:

```bash
ollama pull qwen2.5:7b
```

3. В `.env` включи:

```bash
POSTIT_AI_PROVIDER=ollama
POSTIT_OLLAMA_BASE_URL=http://localhost:11434
POSTIT_OLLAMA_MODEL=qwen2.5:7b
```

4. Запусти приложение:

```bash
make dev
```

## 3. Docker без Ollama

```bash
cp .env.example .env
docker compose up --build
```

Открой `http://localhost:8000/`.

В этом режиме приложение стартует с `mock` AI, если ты не менял `.env`.

## 4. Docker вместе с Ollama

1. В `.env` выставь:

```bash
POSTIT_AI_PROVIDER=ollama
POSTIT_OLLAMA_BASE_URL=http://ollama:11434
POSTIT_OLLAMA_MODEL=qwen2.5:7b
```

2. Подними сервисы:

```bash
docker compose --profile ollama up --build -d
```

3. Один раз загрузи модель внутрь контейнера:

```bash
docker exec -it postit-agent-ollama ollama pull qwen2.5:7b
```

4. Проверь, что Ollama отвечает:

```bash
curl http://localhost:11434/api/tags
```

## 5. Автотесты

```bash
make test
```

Сейчас тесты покрывают:

- mock extraction;
- базовую валидацию адаптеров;
- генерацию Yandex feed;
- сборку усиленных Ollama prompt templates;
- нормализацию ответа LLM в типы домена.

## 6. Быстрый smoke-check API

```bash
curl http://localhost:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok","environment":"development"}
```

## 7. Ручной smoke-сценарий через UI

1. Зарегистрируйся на главной странице.
2. Заполни профиль риелтора.
3. Создай объект.
4. Загрузи 5+ фото.
5. Вставь транскрипт:

```text
Двушка, 54 квадрата, 5 этаж из 17, кухня 10, монолит, хороший ремонт, окна во двор, цена 12 миллионов 500.
```

6. Нажми:
   - `Сохранить голосовую заметку`
   - `Извлечь поля`
   - `Сгенерировать текст`
   - `Проверить`
   - `Собрать фиды`
7. Проверь:
   - что появился ZIP;
   - что открываются feed URLs;
   - что в snapshot справа лежат извлеченные поля и platform payloads.

## 8. Что смотреть при проблемах

- backend logs:

```bash
docker compose logs -f app
```

- ollama logs:

```bash
docker compose logs -f ollama
```

- локальная база и артефакты:
  - `data/postit_agent.sqlite3`
  - `data/storage`
  - `data/feeds`
  - `data/exports`
