# MVP: AI-мониторинг успеваемости

Веб-приложение на FastAPI для 3 ролей:
- `teacher` создаёт лекции, генерирует/редактирует тесты, публикует их и смотрит аналитику группы.
- `student` проходит опубликованные тесты и смотрит личную аналитику.
- `admin` смотрит список всех пользователей.

## Стек и структура
- Backend: FastAPI (`main.py`)
- Шаблоны: Jinja2 (`app/templates`)
- Статика: CSS (`app/static`)
- БД: SQLite локально / PostgreSQL в проде через `DATABASE_URL`
- Генерация вопросов: Epstein engine (OpenAI + fallback) (`app/ai.py`)
- Авторизация: сессии через `SessionMiddleware`

Ключевые файлы:
- `main.py` - маршруты и основная бизнес-логика
- `app/db.py` - подключение и создание таблиц
- `app/ai.py` - генерация вопросов
- `app/security.py` - хеширование пароля и соль

## Запуск
1. Установить зависимости:
```bash
python -m pip install -r requirements.txt
```

2. Запустить сервер:
```bash
# В PowerShell (если политика исполнения блокирует скрипты, временно разрешаем на сессию):
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned -Force
.\venv\Scripts\Activate.ps1
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Альтернатива (не требует активации venv в PowerShell):
```powershell
.\venv\Scripts\python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

3. Открыть в браузере:
`http://127.0.0.1:8000`

При старте автоматически вызывается `init_db()` и создаются таблицы, если их ещё нет.
Если задан `DATABASE_URL` с `postgres://` или `postgresql://`, приложение автоматически использует PostgreSQL.

4. (Опционально) заполнить БД демонстрационными данными:
```bash
python scripts/seed_demo_data.py
```
Если БД уже заполнена:
```bash
python scripts/seed_demo_data.py --force
```
После сидирования доступны тестовые пользователи:
- `admin@example.com / Admin123!`
- `teacher1@example.com / Teacher123!`
- `teacher2@example.com / Teacher123!`
- `student1@example.com` ... `student6@example.com / Student123!`

5. (Опционально) создать преподавателей по дисциплинам:
```bash
python scripts/seed_disciplines_teachers.py
```
Скрипт создаёт 5 дисциплин и преподавателей с логинами:
- `discipline.teacher1@example.com / Teacher123!`
- `discipline.teacher2@example.com / Teacher123!`
- `discipline.teacher3@example.com / Teacher123!`
- `discipline.teacher4@example.com / Teacher123!`
- `discipline.teacher5@example.com / Teacher123!`

6. (Опционально) создать тесты по всем дисциплинам и заполнить прохождения:
```bash
python scripts/seed_discipline_progress.py
```
Скрипт создаёт/дополняет:
- тесты по дисциплинам;
- студентов в разных группах для дисциплин;
- попытки прохождения с разными баллами (сильные/средние/слабые профили).

Важно про вход преподавателей:
- Для временной авторизации преподавателя при входе используется мастер‑код: `Dementor`.
  При выборе роли `Преподаватель` в форме входа нужно ввести этот код в поле "Код преподавателя".
  (Рекомендую заменить код на переменную окружения и не держать его в коде.)

## Почему запуск через `uvicorn`, а не `py main.py`
- Это ASGI-приложение FastAPI, ему нужен ASGI-сервер.
- Команда `uvicorn main:app --reload` означает:
  - `main` - модуль (`main.py`)
  - `app` - объект приложения `FastAPI()`
  - `--reload` - авто-перезапуск сервера при изменениях кода
- `py main.py` просто запускает Python-файл как скрипт и сам по себе сервер не поднимет.
- В текущем проекте в `main.py` нет блока `if __name__ == "__main__": uvicorn.run(...)`, поэтому правильный способ запуска - именно через `uvicorn`.

## Частые проблемы запуска
- Ошибка `uvicorn is not recognized`:
  - не активировано виртуальное окружение или не установлены зависимости.
- Открывается пусто/ошибка подключения:
  - проверьте, что сервер реально запущен и слушает `127.0.0.1:8000`.
- Ключ API не работает:
  - проверьте, что `.env` лежит в корне проекта или что переменные окружения реально заданы для процесса.

Примечание по Swagger/Redoc:
- Интерфейсы документации FastAPI доступны по URL `/docs` (Swagger UI) и `/redoc` (ReDoc),
  но ссылки на них удалены из интерфейса — доступ по URL остаётся.

## Переменные окружения
Используются переменные:
- `OPENAI_API_KEY` - ключ OpenAI API
- `OPENAI_MODEL` - модель OpenAI (рекомендуется `gpt-4.1` для точности)

Новый: `TEACHER_KEY` - мастер‑код для входа преподавателей (рекомендуется задать в окружении).
Пример (PowerShell, текущая сессия):
```powershell
$env:TEACHER_KEY = "Dementor"
```

Пример для PowerShell (только на текущую сессию):
```powershell
$env:OPENAI_API_KEY="ваш_openai_api_key"
$env:OPENAI_MODEL="gpt-4.1"
```

Важно по ChatGPT подписке:
- Подписка ChatGPT Plus/Pro обычно **не** включает бесплатные API-кредиты OpenAI.
- Для API нужен отдельный API-ключ и биллинг в OpenAI Platform.

`.env` в корне проекта подгружается автоматически, если установлен `python-dotenv`.

## Render и БД
- На Render файловая система web service эфемерная, поэтому `app/app.db` нельзя считать продовой базой.
- Для нормального продакшена используйте PostgreSQL и переменную `DATABASE_URL`.
- В репозитории добавлен `render.yaml`, который:
  - создаёт web service;
  - создаёт PostgreSQL `campusplus-db`;
  - пробрасывает `DATABASE_URL` из базы в приложение;
  - генерирует `SESSION_SECRET_KEY`;
  - позволяет задать bootstrap-админа через env.

Минимальный набор env для Render:
- `DATABASE_URL`
- `SESSION_SECRET_KEY`
- `BOOTSTRAP_ADMIN_LOGIN`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `BOOTSTRAP_ADMIN_FULL_NAME` (опционально)

После первого старта на Render:
- схема БД создастся автоматически;
- bootstrap-админ создастся автоматически;
- дальше пользователей можно заводить уже через интерфейс.

## Как работает программа по шагам

### 1) Регистрация и вход
- `POST /register`: создаёт пользователя с ролью `student|teacher|admin`.
- Пароль не хранится в открытом виде:
  - генерируется соль (`new_salt()`)
  - сохраняется `sha256(salt + password)` (`hash_password`)
- `POST /login`: сравнивает хеш введённого пароля с БД.
- При успешном входе в сессию записывается `user_id`.

### 2) Роутинг по ролям
- После входа `/dashboard` показывает разные разделы для каждой роли.
- На каждом защищённом маршруте проверяется роль:
  - teacher: только teacher-роуты
  - student: только student-роуты
  - admin: только admin-роуты

### 3) Сценарий преподавателя (`teacher`)
1. Создаёт лекцию (`/teacher/lectures/new`) одним из способов:
   - вставляет текст вручную;
   - загружает файл `.txt`, `.docx` или `.pdf`.
  - вставляет ссылки на статьи/страницы (например Wikipedia), система сама извлекает текст.
   Минимальный размер итогового текста лекции: 20 символов.
2. Открывает лекцию (`/teacher/lectures/{lecture_id}`).
3. Нажимает генерацию теста и выбирает параметры:
  - количество вопросов (1..50)
  - сложность (`easy` / `medium` / `hard`)
   - создаётся `tests` со статусом `draft`
  - создаётся заданное число вопросов в `questions`
4. Редактирует вопросы/варианты/правильные ответы (`/teacher/tests/{test_id}/edit`).
5. Публикует тест (`/teacher/tests/{test_id}/publish`) -> статус `published`.
6. Смотрит аналитику группы (`/teacher/analytics`).

### 4) Сценарий студента (`student`)
1. Видит список опубликованных тестов (`/student/tests`).
2. Проходит тест (`/student/tests/{test_id}/take`).
3. После отправки:
   - считается процент правильных ответов `score` (0..100)
   - сохраняется попытка в `attempts`
   - сохраняются ответы по каждому вопросу в `answers`
4. Переходит в личную аналитику (`/student/analytics`).

### 5) Сценарий администратора (`admin`)
- `GET /admin/users`: видит таблицу пользователей (`id`, роль, ФИО, email).

## Генерация вопросов (AI + fallback)
Логика в `app/ai.py`:
1. Epstein engine использует OpenAI (`gpt-4.1`) для генерации вопросов.
2. Если OpenAI недоступен/ошибся, включается локальная эвристика.
4. В промпте запрашивается строгий JSON:
   - `text`
   - `options` (4 варианта)
   - `correct_index`
5. Ответ очищается и парсится.
6. Данные нормализуются:
   - некорректные вопросы отбрасываются
   - варианты приводятся к 4 элементам
   - `correct_index` приводится к допустимому диапазону
7. Если AI недоступен/ошибся, включается эвристика:
   - из текста лекции извлекаются предложения и частые ключевые слова
   - собираются простые вопросы с 4 вариантами

Итог: тест создаётся даже без OpenAI, но качество вопросов ниже.

## Аналитика: что считается

Для студента (`/student/analytics`):
- `avg`: средний балл по всем попыткам
- `trend`: разница между последней и предыдущей попыткой
- `best` / `worst`: лучший и худший результат
- `last7`: средний балл за последние 7 дней
- `recent`: 5 последних попыток
- `per_test`: последний результат по каждому тесту
- `sparkline`: SVG-линия по последним до 10 результатам
- повторное прохождение одного и того же теста запрещено (одна попытка на тест)

Для студента в личном кабинете (`/dashboard`):
- блок `История тестирований` с последними попытками и переходом в разбор ошибок
- блок `Дисциплины` по закреплённому преподавателю

Для преподавателя (`/teacher/analytics`):
- `total_attempts`: всего попыток студентов по его тестам
- `unique_students`: число уникальных студентов
- `overall_avg`: средний балл по всем попыткам
- `per_test`: по каждому тесту попытки/средний/лучший/худший
- `student_avg`: средний балл по каждому студенту

Для преподавателя v2 (`/v2/teacher`):
- входная страница `Дисциплины`
- в разделах `Тесты` и `Успеваемость` добавлена фильтрация по дисциплине
- в разделе `Студенты` доступны действия: редактировать, удалить, переместить по группам

## Схема БД
Таблицы создаются в `app/db.py`:
- `users` - аккаунты
- `lectures` - лекции преподавателей
- `tests` - тесты (draft/published)
- `questions` - вопросы тестов
- `attempts` - попытки прохождения тестов
- `answers` - ответы по вопросам в рамках попытки

Связи:
- `lectures.teacher_id -> users.id`
- `tests.lecture_id -> lectures.id`
- `questions.test_id -> tests.id`
- `attempts.test_id -> tests.id`
- `attempts.student_id -> users.id`
- `answers.attempt_id -> attempts.id`
- `answers.question_id -> questions.id`

## Основные маршруты
- Публичные: `/`, `/register`, `/login`
- Общие: `/dashboard`, `/logout`
- Teacher:
  - `/teacher/lectures`
  - `/teacher/lectures/new`
  - `/teacher/lectures/{lecture_id}`
  - `/teacher/lectures/{lecture_id}/generate`
  - `/teacher/tests/{test_id}/edit`
  - `/teacher/tests/{test_id}/publish`
  - `/teacher/analytics`
- Student:
  - `/student/tests`
  - `/student/tests/{test_id}/take`
  - `/student/analytics`
- Admin:
  - `/admin/users`

## Ограничения текущего MVP
- Секрет сессии в коде (`dev-secret-change`), для продакшена нужно вынести в переменную окружения.
- Нет CSRF-защиты форм.
- Нет миграций БД (используется `CREATE TABLE IF NOT EXISTS`).
- Логика авторизации/ролей базовая, без granular permissions.
- Для Word поддерживается формат `.docx` (старый `.doc` не поддерживается).

## SQL-задачи для практики ручного тестирования

Ниже 10 задач по реальной БД проекта CampusPlus. Все примеры написаны в простом SQL без специфичных функций, чтобы запросы можно было запускать и на SQLite, и на PostgreSQL. В условиях используй свои реальные значения групп, преподавателей, дисциплин и логинов.

### Задача #1.
**Суть задачи:**  
Выбери всех пользователей с ролью `student` из группы `БИ-41.1`, отсортируй их по ФИО.

```sql
SELECT id, full_name, email, student_group
FROM users
WHERE role = 'student'
  AND student_group = 'БИ-41.1'
ORDER BY full_name;
```

### Задача #2.
**Суть задачи:**  
Выведи список преподавателей и дисциплин, которые за ними закреплены.

```sql
SELECT
    u.id AS teacher_id,
    u.full_name AS teacher_name,
    u.email AS teacher_email,
    d.id AS discipline_id,
    d.name AS discipline_name
FROM teacher_disciplines td
JOIN users u ON u.id = td.teacher_id
JOIN disciplines d ON d.id = td.discipline_id
WHERE u.role = 'teacher'
ORDER BY u.full_name, d.name;
```

### Задача #3.
**Суть задачи:**  
Покажи, какие группы имеют доступ к дисциплине `Администрирование информационных систем`, и какой преподаватель ведёт эту дисциплину у каждой группы.

```sql
SELECT
    d.name AS discipline_name,
    ta.group_name,
    u.full_name AS teacher_name,
    u.email AS teacher_email
FROM teaching_assignments ta
JOIN disciplines d ON d.id = ta.discipline_id
JOIN users u ON u.id = ta.teacher_id
WHERE d.name = 'Администрирование информационных систем'
ORDER BY ta.group_name, u.full_name;
```

### Задача #4.
**Суть задачи:**  
Найди все опубликованные тесты, укажи название теста, лекции, дисциплины и преподавателя, который их создал.

```sql
SELECT
    t.id AS test_id,
    t.title AS test_title,
    l.title AS lecture_title,
    d.name AS discipline_name,
    u.full_name AS teacher_name,
    t.created_at
FROM tests t
JOIN lectures l ON l.id = t.lecture_id
LEFT JOIN disciplines d ON d.id = l.discipline_id
JOIN users u ON u.id = l.teacher_id
WHERE t.status = 'published'
ORDER BY t.created_at DESC, t.id DESC;
```

### Задача #5.
**Суть задачи:**  
Покажи все попытки прохождения тестов студентами: кто проходил, какой тест, на сколько баллов и когда.

```sql
SELECT
    a.id AS attempt_id,
    s.full_name AS student_name,
    s.email AS student_email,
    t.title AS test_title,
    a.score,
    a.taken_at
FROM attempts a
JOIN users s ON s.id = a.student_id
JOIN tests t ON t.id = a.test_id
WHERE s.role = 'student'
ORDER BY a.taken_at DESC, a.id DESC;
```

### Задача #6.
**Суть задачи:**  
Найди студентов, которые проходили тесты на результат ниже `60` баллов. Покажи студента, тест, дисциплину и балл.

```sql
SELECT
    s.full_name AS student_name,
    s.student_group,
    t.title AS test_title,
    d.name AS discipline_name,
    a.score,
    a.taken_at
FROM attempts a
JOIN users s ON s.id = a.student_id
JOIN tests t ON t.id = a.test_id
JOIN lectures l ON l.id = t.lecture_id
LEFT JOIN disciplines d ON d.id = l.discipline_id
WHERE a.score < 60
ORDER BY a.score ASC, a.taken_at DESC;
```

### Задача #7.
**Суть задачи:**  
Посчитай, сколько лекций и сколько тестов есть по каждой дисциплине.

```sql
SELECT
    d.name AS discipline_name,
    COUNT(DISTINCT l.id) AS lectures_count,
    COUNT(DISTINCT t.id) AS tests_count
FROM disciplines d
LEFT JOIN lectures l ON l.discipline_id = d.id
LEFT JOIN tests t ON t.lecture_id = l.id
GROUP BY d.id, d.name
ORDER BY d.name;
```

### Задача #8.
**Суть задачи:**  
Покажи топ-5 студентов по среднему баллу за все попытки.

```sql
SELECT
    s.id AS student_id,
    s.full_name AS student_name,
    s.student_group,
    ROUND(AVG(a.score), 2) AS avg_score,
    COUNT(a.id) AS attempts_count
FROM users s
JOIN attempts a ON a.student_id = s.id
WHERE s.role = 'student'
GROUP BY s.id, s.full_name, s.student_group
ORDER BY avg_score DESC, attempts_count DESC, s.full_name
LIMIT 5;
```

### Задача #9.
**Суть задачи:**  
Покажи все ошибочные ответы конкретного студента: какой тест, какой вопрос, индекс выбранного ответа и индекс правильного ответа.

```sql
SELECT
    s.full_name AS student_name,
    t.title AS test_title,
    q.text AS question_text,
    ans.selected_index,
    q.correct_index,
    a.taken_at
FROM answers ans
JOIN attempts a ON a.id = ans.attempt_id
JOIN users s ON s.id = a.student_id
JOIN questions q ON q.id = ans.question_id
JOIN tests t ON t.id = a.test_id
WHERE s.email = 'student@example.com'
  AND ans.is_correct = 0
ORDER BY a.taken_at DESC, t.title, q.id;
```

### Задача #10.
**Суть задачи:**  
Покажи группы, преподавателей и дисциплины, которые они ведут именно у этих групп. Это задача на несколько `JOIN`, чтобы проверить понимание связей в проекте.

```sql
SELECT
    gt.group_name,
    u.full_name AS teacher_name,
    u.email AS teacher_email,
    d.name AS discipline_name
FROM group_teachers gt
JOIN users u ON u.id = gt.teacher_id
LEFT JOIN teaching_assignments ta
       ON ta.teacher_id = gt.teacher_id
      AND ta.group_name = gt.group_name
LEFT JOIN disciplines d ON d.id = ta.discipline_id
ORDER BY gt.group_name, u.full_name, d.name;
```
