# Функциональное задание v3: DOCX Summarizer (итоговое)
> Полная актуальная спецификация. Включает всё из v1 и v2 плюс итоговые требования к деплою и производительности.

---

## 1. Описание проекта

Веб-приложение для конвертации DOCX-файлов в TXT и суммаризации содержимого с помощью модели Qwen3-Omni-Flash. Результат суммаризации выгружается в CSV.

---

## 2. Функциональные требования

### Пользовательский сценарий

1. Пользователь загружает до **20 DOCX-файлов** через drag-and-drop или кнопку выбора
2. Нажимает **«Конвертировать»** → получает ZIP-архив с TXT-файлами
3. Нажимает **«Отправить на суммаризацию»** → сервер читает те же DOCX, конвертирует их сам и отправляет текст в Qwen API
4. Получает **CSV-файл** с колонками «Имя файла» и «Суммаризация»

### Лимиты

- Максимум **20 файлов** за один раз
- Максимальный размер файла: **10 МБ** (FastAPI default)

---

## 3. Архитектура

```
Браузер  ──POST /convert──▶  FastAPI  ──python-docx──▶  ZIP с TXT
Браузер  ──POST /summarize──▶ FastAPI  ──python-docx──▶  Qwen API ──▶  CSV
```

Фронтенд — single-page HTML/CSS/JS (`static/index.html`), отдаётся FastAPI как статика.

---

## 4. Технический стек

| Компонент | Технология |
|---|---|
| Бэкенд | FastAPI + uvicorn |
| DOCX → TXT | python-docx |
| HTTP-клиент для API | httpx (AsyncClient, прямые запросы) |
| ИИ-модель | Qwen3-Omni-Flash (Alibaba Cloud ModelStudio) |
| Переменные окружения | python-dotenv (локально) |

> **Важно:** openai SDK **не используется**. Вместо него — прямые httpx-запросы с явной UTF-8 сериализацией JSON. Это обязательно из-за бага в openai SDK v2.x с кириллическими заголовками.

---

## 5. API-интеграция (Qwen3-Omni-Flash)

| Параметр | Значение |
|---|---|
| Endpoint | `https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions` |
| Модель | `qwen3-omni-flash` |
| Режим | Только `stream: true` |
| Переменная ключа | `QWEN_API_KEY` |
| Получить ключ | [ModelStudio Console](https://modelstudio.console.alibabacloud.com/) → API Keys |

Промпт суммаризации:
```
Сделай суммаризацию текста на русском языке.
Объём — не более 1000 символов.
Структурируй по логическим разделам документа, используя короткие подзаголовки.
Отвечай только суммаризацией, без вводных фраз.
```

---

## 6. Файловая структура

```
frog_converter/
├── main.py                              # FastAPI-приложение
├── requirements.txt                     # Зависимости Python
├── amvera.yml                           # Конфиг деплоя Amvera
├── .env                                 # API-ключ (локально, не в git)
├── .gitignore                           # Исключает .env и __pycache__
├── functional_spec.md                   # Исходное ТЗ
├── functional_spec_v2.md                # Изменения и баги
├── functional_spec_v3.md                # Этот файл (итоговый)
├── deploy_amvera.md                     # Инструкция по деплою
├── image_slide_ready_transparent.png   # Исходник логотипа
└── static/
    ├── index.html                       # Весь фронтенд
    └── frog.png                         # Логотип-маскот
```

---

## 7. requirements.txt

```
fastapi
uvicorn
python-docx
httpx
python-multipart
python-dotenv
```

---

## 8. Итоговый amvera.yml

```yaml
meta:
  environment: python
  toolVersion: 3.11
  toolchain:
    name: pip
run:
  containerPort: 8000
  command: uvicorn main:app --host 0.0.0.0 --port 8000
```

### Обязательные требования к amvera.yml

| Поле | Значение | Примечание |
|---|---|---|
| `meta.environment` | `python` | |
| `meta.toolVersion` | `3.11` | **Без кавычек** |
| `meta.toolchain.name` | `pip` | **Обязательное поле**, без него — ошибка конфигурации |
| `run.containerPort` | `8000` | Порт uvicorn |
| `run.command` | `uvicorn main:app --host 0.0.0.0 --port 8000` | **`--host 0.0.0.0` обязателен** (иначе приложение недоступно снаружи) |

> ❌ Поле `build.installDependencies` **не существует** в Amvera — вызывает ошибку. Pip читает `requirements.txt` автоматически.

---

## 9. Переменные окружения

| Переменная | Локально | Amvera |
|---|---|---|
| `QWEN_API_KEY` | `.env` файл | Раздел «Переменные» в кабинете |

---

## 10. Команда локального запуска

```bash
cd /path/to/frog_converter
# Вставить ключ в .env: QWEN_API_KEY=sk-...
pip3 install -r requirements.txt
PYTHONUTF8=1 python3 -m uvicorn main:app --reload --port 8000
```

> `PYTHONUTF8=1` — обязателен на Python 3.9 macOS для корректной обработки кириллицы.

---

## 11. Параллельная суммаризация (asyncio.gather)

**Проблема:** при обработке 15 файлов последовательно суммаризация занимала ~150с, что превышало лимит nginx на Amvera (504 Gateway Timeout).

**Решение:** все файлы суммаризируются **одновременно** через `asyncio.gather()`. Итоговое время ≈ времени самого долгого файла (~10–20с).

```python
# Параллельная обработка — вместо последовательного цикла
rows = list(await asyncio.gather(*[process_one(u) for u in files]))
```

| Режим | 15 файлов × 10с | Вписывается в 60с? |
|---|---|---|
| Последовательный (было) | ~150с | ❌ 504 Timeout |
| Параллельный (стало) | ~10–20с | ✅ Работает |

