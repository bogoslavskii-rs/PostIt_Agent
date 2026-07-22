# Запуск и тестирование

Документ синхронизирован с текущим состоянием проекта на Wednesday, July 22, 2026.

## 1. Локальный запуск без Docker

```bash
cp .env.example .env
make install
make dev
```

Открой:

- `http://localhost:8000/`
- `http://localhost:8000/docs`
- `http://localhost:8000/health`
- `http://localhost:8000/ready`

## 2. Mock-flow через UI

1. Зарегистрируйся.
2. Заполни профиль.
3. Создай объект и включи `Карточка подтверждена`.
4. Загрузи минимум 5 фото.
5. Добавь аудиофайл или вставь mock-транскрипт:

```text
Двушка, 54 квадрата, 5 этаж из 17, кухня 10, монолит, хороший ремонт, окна во двор, цена 12 миллионов 500.
```

6. Нажми:
   - `Сохранить голосовую заметку`
   - `Извлечь поля`
   - в блоке `AI Evidence` подтверждай или отклоняй подсказки
   - `Сгенерировать текст`
   - `Проверить`
   - `Собрать фиды`
7. Проверь:
   - ZIP assisted-пакет;
   - feed URL для `CIAN` и `Yandex Realty`;
   - HTML/JSON/TXT/DOCX внутри архива;
   - статусы `PublicationJob`.

## 3. Автотесты

```bash
make test
```

Локально проверено:

- `9 passed`.

## 4. Quick smoke в CLI

```bash
PYTHONPATH=src .venv/bin/python -m py_compile src/postit_agent/*.py tests/*.py
PYTHONPATH=src .venv/bin/pytest -q
```

## 5. Docker

```bash
cp .env.example .env
docker compose up --build
```

Этот режим тоже стартует в `mock`-конфигурации, если не менялись AI/STT env-переменные.

## 6. Ограничения текущей версии

- SQLite остаётся основным storage-слоем локального MVP.
- Нет Redis worker-а и внешней очереди задач.
- Нет подтверждённых прямых publish API на площадки.
- `mark-published` используется как честная ручная фиксация результата после внешнего действия пользователя.
