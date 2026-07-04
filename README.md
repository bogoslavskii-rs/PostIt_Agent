# PostIt Agent

Python-first MVP scaffold для приложения, которое помогает риелтору:

- быстро собрать единую карточку объекта;
- прогнать голосовое описание через AI extraction;
- сгенерировать тексты под площадки;
- получить feed/XML/YRL и ZIP-пакет для ручной или semi-automatic публикации.

Рабочая ветка реализации: `feature/ai-realtor-app`.

## Что уже есть

- `FastAPI` backend с основными MVP-endpoint'ами из ТЗ.
- Локальная `sqlite`-персистентность для dev-режима без внешней БД.
- AI-слой с двумя режимами:
  - `mock` по умолчанию;
  - `ollama` с усиленными prompt templates под русскую вторичную недвижимость.
- STT-слой с `mock` и заготовкой под `whisper.cpp`.
- Адаптеры площадок:
  - `cian`
  - `yandex_realty`
  - `youla`
  - `avito`
  - `domclick`
- Генерация:
  - пользовательских feed-файлов;
  - экспортного ZIP;
  - `txt` и `docx` с копирайтом;
  - локальных public URLs для media и feeds.
- Мобильный web-shell `/`, через который можно пройти dev-сценарий end-to-end.
- Docker-упаковка для локального старта проекта.

## Архитектура

```text
src/postit_agent/
  main.py         FastAPI app и HTTP API
  services.py     Оркестрация сценариев MVP
  repository.py   SQLite storage
  ai.py           Mock/Ollama/whisper.cpp integration layer
  adapters.py     Feed adapters per platform
  models.py       Pydantic domain models
  static/         Mobile-first web UI
```

## Быстрый запуск локально

```bash
cp .env.example .env
make install
make dev
```

После старта открывай `http://localhost:8000/`.

По умолчанию проект поднимается в `mock`-режиме и не зависит от локальной LLM.

## Быстрый запуск в Docker

```bash
cp .env.example .env
docker compose up --build
```

После этого приложение будет доступно на `http://localhost:8000/`.

## Как включить Ollama

### Вариант 1. Ollama у тебя на машине

```bash
ollama pull qwen2.5:7b
```

В `.env`:

```bash
POSTIT_AI_PROVIDER=ollama
POSTIT_OLLAMA_BASE_URL=http://localhost:11434
POSTIT_OLLAMA_MODEL=qwen2.5:7b
```

После этого запускай backend обычным способом:

```bash
make dev
```

### Вариант 2. Ollama в Docker

В `.env`:

```bash
POSTIT_AI_PROVIDER=ollama
POSTIT_OLLAMA_BASE_URL=http://ollama:11434
POSTIT_OLLAMA_MODEL=qwen2.5:7b
```

Подними стек:

```bash
docker compose --profile ollama up --build -d
```

Потом один раз закачай модель в контейнер:

```bash
docker exec -it postit-agent-ollama ollama pull qwen2.5:7b
```

Если Ollama недоступен или вернет плохой JSON, сервис мягко откатится на `mock`.

## Что я усилил в Ollama-режиме

- extraction prompt теперь возвращает полную схему полей, а не случайный частичный JSON;
- добавлены нормализации под русскую недвижимость:
  - `монолит -> monolith`
  - `хороший ремонт -> good`
  - `во двор -> yard`
  - `12 500 000 ₽ -> 12500000`
- copy prompt теперь отдельно учитывает ограничения `Avito`, `CIAN`, `Yandex Realty`, `Youla`, `Domclick`;
- запросы в Ollama идут с настраиваемыми `temperature`, `top_p`, `repeat_penalty`, `num_ctx`, `num_predict`.

Настройки лежат в `.env.example`.

## Как тестить

Автотесты:

```bash
make test
```

Быстрый smoke-check:

```bash
curl http://localhost:8000/health
```

Ожидаемый ответ:

```json
{"status":"ok","environment":"development"}
```

Полный пошаговый сценарий запуска, Docker, Ollama и ручной smoke лежит в [docs/TESTING.md](/home/anderrated/work/projects/PostIt_Agent/docs/TESTING.md).

## Dev-сценарий через UI

1. Регистрируешь аккаунт.
2. Заполняешь профиль и режимы автозагрузки по площадкам.
3. Создаешь объект.
4. Грузишь фото.
5. Вставляешь транскрипт голоса.
6. Жмешь:
   - `Извлечь поля`
   - `Сгенерировать текст`
   - `Проверить`
   - `Собрать фиды`
7. На выходе получаешь:
   - feed URL на пользователя и площадку;
   - ZIP-пакет;
   - список platform jobs и ошибок.

## Полезные команды

```bash
make install
make dev
make test
make smoke
make docker-build
make docker-up
make docker-up-ollama
make docker-down
```

## Что важно понимать

- `CIAN`, `Yandex Realty` и `Youla` в коде отражены как feed-first интеграции.
- `Avito` и `Domclick` пока заложены как MVP/draft adapters с честным fallback на `user_assisted` или `manual_export`.
- Dev storage локальный:
  - база: `data/postit_agent.sqlite3`
  - медиа: `data/storage`
  - фиды: `data/feeds`
  - архивы: `data/exports`

## Источники для интеграций

- CIAN XML docs: `https://www.cian.ru/xml_import/doc/`
- Yandex Realty feed docs: `https://yandex.ru/support/realty/ru/feed/content-requirements`
- Youla autoload docs: `https://help.youla.ru/level-3/avtozagruzka-obyavleniy`
- Avito autoload template: `https://www.avito.ru/autoload/documentation/templates/67067?fileFormat=xml`

## Следующий разумный шаг

- заменить dev `sqlite` на `PostgreSQL`;
- вынести фоновые задачи в `Redis + worker`;
- добавить реальный mobile-клиент;
- ужесточить схемы feed-валидаторов под боевые форматы площадок;
- подключить `whisper.cpp` или другой локальный STT по-настоящему.
