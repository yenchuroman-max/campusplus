# SQL-задачи для практики

Ниже 10 SQL-задач по реальной базе проекта CampusPlus.  
Уровень: **начинающий / начинающий-средний**.  
Все запросы завязаны на настоящие таблицы проекта: `users`, `groups`, `disciplines`, `lectures`, `tests`, `attempts`, `teacher_disciplines`, `teaching_assignments`.

Если в вашей БД нет конкретного значения из примера, просто подставьте своё.

---

## Задача #1.
**Суть задачи:**  
Выбери всех студентов из группы `БИ-41.1`.

```sql
SELECT id, full_name, email, student_group
FROM users
WHERE role = 'student'
  AND student_group = 'БИ-41.1'
ORDER BY full_name;
```

---

## Задача #2.
**Суть задачи:**  
Выбери всех преподавателей системы.

```sql
SELECT id, full_name, email
FROM users
WHERE role = 'teacher'
ORDER BY full_name;
```

---

## Задача #3.
**Суть задачи:**  
Покажи все дисциплины, которые есть в системе.

```sql
SELECT id, name
FROM disciplines
ORDER BY name;
```

---

## Задача #4.
**Суть задачи:**  
Покажи все лекции и укажи, какой преподаватель их создал.

```sql
SELECT
    l.id,
    l.title AS lecture_title,
    u.full_name AS teacher_name,
    l.created_at
FROM lectures l
JOIN users u ON u.id = l.teacher_id
ORDER BY l.created_at DESC;
```

---

## Задача #5.
**Суть задачи:**  
Покажи все опубликованные тесты и название лекции, к которой они относятся.

```sql
SELECT
    t.id,
    t.title AS test_title,
    l.title AS lecture_title,
    t.status
FROM tests t
JOIN lectures l ON l.id = t.lecture_id
WHERE t.status = 'published'
ORDER BY t.id;
```

---

## Задача #6.
**Суть задачи:**  
Покажи всех студентов, которые хотя бы один раз проходили тест.

```sql
SELECT DISTINCT
    u.id,
    u.full_name,
    u.email,
    u.student_group
FROM attempts a
JOIN users u ON u.id = a.student_id
ORDER BY u.full_name;
```

---

## Задача #7.
**Суть задачи:**  
Посчитай, сколько попыток прохождения тестов сделал каждый студент.

```sql
SELECT
    u.full_name AS student_name,
    COUNT(a.id) AS attempts_count
FROM users u
LEFT JOIN attempts a ON a.student_id = u.id
WHERE u.role = 'student'
GROUP BY u.id, u.full_name
ORDER BY attempts_count DESC, u.full_name;
```

---

## Задача #8.
**Суть задачи:**  
Покажи средний балл по каждому тесту.

```sql
SELECT
    t.id,
    t.title AS test_title,
    ROUND(AVG(a.score), 2) AS avg_score
FROM tests t
LEFT JOIN attempts a ON a.test_id = t.id
GROUP BY t.id, t.title
ORDER BY t.title;
```

---

## Задача #9.
**Суть задачи:**  
Покажи, какие дисциплины закреплены за преподавателями.

```sql
SELECT
    u.full_name AS teacher_name,
    d.name AS discipline_name
FROM teacher_disciplines td
JOIN users u ON u.id = td.teacher_id
JOIN disciplines d ON d.id = td.discipline_id
ORDER BY u.full_name, d.name;
```

---

## Задача #10.
**Суть задачи:**  
Покажи, какие группы имеют доступ к дисциплине `Администрирование информационных систем`.

```sql
SELECT
    d.name AS discipline_name,
    ta.group_name,
    u.full_name AS teacher_name
FROM teaching_assignments ta
JOIN disciplines d ON d.id = ta.discipline_id
JOIN users u ON u.id = ta.teacher_id
WHERE d.name = 'Администрирование информационных систем'
ORDER BY ta.group_name, u.full_name;
```

---

Если понадобится, можно сделать ещё один набор:
- только на `SELECT`
- только на `JOIN`
- только на `GROUP BY`
- без готовых решений, чтобы давать студентам как самостоятельную практику
