# -*- coding: utf-8 -*-
"""
Export UML diagrams from presentation as PNG images + generate QR code.
Uses Playwright to render SVGs at high resolution.

Output: presentation_assets/diagrams/  (5 PNGs + 1 QR)
"""
import pathlib, sys, textwrap
sys.stdout.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "presentation_assets" / "diagrams"
OUT_DIR.mkdir(parents=True, exist_ok=True)

VKR_URL = "https://campusplus.onrender.com/vkr"


# ── 1. Use-Case diagram ──────────────────────────────────────
USE_CASE_SVG = textwrap.dedent("""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 820 520" style="background:#fff">
<style>
  text{font-family:'Segoe UI',sans-serif}
  .actor{fill:#18304f;font-weight:700;font-size:15px}
  .use{fill:#fff;stroke:#8ea6c7;stroke-width:2;rx:20}
  .use-text{fill:#18304f;font-size:12px;font-weight:600;text-anchor:middle}
  .line{stroke:#8ea6c7;stroke-width:1.5;fill:none}
  .sysbox{fill:none;stroke:#8ea6c7;stroke-width:2;stroke-dasharray:8 4;rx:16}
  .role-rect{fill:#eaf1fb;stroke:#8ea6c7;stroke-width:2;rx:12}
  .title{fill:#18304f;font-weight:800;font-size:18px;text-anchor:middle}
</style>
<text x="410" y="32" class="title">Диаграмма прецедентов — КампусПлюс</text>
<rect x="160" y="50" width="500" height="440" class="sysbox"/>
<text x="410" y="74" style="text-anchor:middle;fill:#6f88c9;font-size:13px;font-weight:700">Веб-сервис КампусПлюс</text>

<!-- Actors left -->
<rect x="10" y="90" width="130" height="36" class="role-rect"/>
<text x="75" y="114" class="actor" text-anchor="middle">Преподаватель</text>

<rect x="10" y="350" width="130" height="36" class="role-rect"/>
<text x="75" y="374" class="actor" text-anchor="middle">Администратор</text>

<!-- Actors right -->
<rect x="680" y="90" width="130" height="36" class="role-rect"/>
<text x="745" y="114" class="actor" text-anchor="middle">Студент</text>

<rect x="680" y="350" width="130" height="36" class="role-rect"/>
<text x="745" y="374" class="actor" text-anchor="middle">AI-модуль</text>

<!-- Use cases -->
<ellipse cx="380" cy="110" rx="130" ry="22" class="use"/>
<text x="380" y="115" class="use-text">Создать лекцию</text>
<line x1="140" y1="108" x2="250" y2="110" class="line"/>

<ellipse cx="380" cy="170" rx="160" ry="22" class="use"/>
<text x="380" y="175" class="use-text">Сгенерировать тест (AI)</text>
<line x1="140" y1="108" x2="220" y2="170" class="line"/>
<line x1="680" y1="368" x2="540" y2="170" class="line"/>

<ellipse cx="380" cy="230" rx="160" ry="22" class="use"/>
<text x="380" y="235" class="use-text">Собрать тест вручную</text>
<line x1="140" y1="108" x2="220" y2="230" class="line"/>

<ellipse cx="380" cy="290" rx="160" ry="22" class="use"/>
<text x="380" y="295" class="use-text">Опубликовать тест и QR</text>
<line x1="140" y1="108" x2="220" y2="290" class="line"/>

<ellipse cx="380" cy="350" rx="130" ry="22" class="use"/>
<text x="380" y="355" class="use-text">Пройти тест</text>
<line x1="680" y1="108" x2="510" y2="350" class="line"/>

<ellipse cx="380" cy="410" rx="160" ry="22" class="use"/>
<text x="380" y="415" class="use-text">Просмотреть аналитику</text>
<line x1="140" y1="108" x2="220" y2="410" class="line"/>
<line x1="680" y1="108" x2="540" y2="410" class="line"/>

<ellipse cx="380" cy="466" rx="170" ry="22" class="use"/>
<text x="380" y="471" class="use-text">Управлять пользователями и группами</text>
<line x1="140" y1="368" x2="210" y2="466" class="line"/>
</svg>
""")

# ── 2. Class diagram ─────────────────────────────────────────
CLASS_SVG = textwrap.dedent("""\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 600" style="background:#fff">
<style>
  text{font-family:'Segoe UI',sans-serif}
  .box{fill:#fff;stroke:#8ea6c7;stroke-width:2}
  .head{fill:#eaf1fb}
  .t{font:700 14px 'Segoe UI';fill:#18304f}
  .f{font:12px 'Segoe UI';fill:#4a6180}
  .pk{fill:#d77463;font:700 10px 'Segoe UI'}
  .fk{fill:#6f88c9;font:700 10px 'Segoe UI'}
  .link{stroke:#7b90b0;stroke-width:2;fill:none}
  .title{fill:#18304f;font:800 18px 'Segoe UI';text-anchor:middle}
</style>
<text x="450" y="28" class="title">Диаграмма классов — КампусПлюс</text>

<!-- User -->
<rect x="10" y="50" width="170" height="130" rx="8" class="box"/>
<rect x="10" y="50" width="170" height="28" rx="8" class="head"/>
<text x="95" y="70" class="t" text-anchor="middle">User</text>
<text x="20" y="96" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="20" y="114" class="f">full_name: str</text>
<text x="20" y="132" class="f">login: str</text>
<text x="20" y="150" class="f">role: str</text>
<text x="20" y="168" class="f"><tspan class="fk">FK</tspan> group_id: int</text>

<!-- Group -->
<rect x="10" y="220" width="170" height="100" rx="8" class="box"/>
<rect x="10" y="220" width="170" height="28" rx="8" class="head"/>
<text x="95" y="240" class="t" text-anchor="middle">Group</text>
<text x="20" y="266" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="20" y="284" class="f">name: str</text>
<text x="20" y="302" class="f">year: int</text>

<!-- Discipline -->
<rect x="240" y="50" width="180" height="120" rx="8" class="box"/>
<rect x="240" y="50" width="180" height="28" rx="8" class="head"/>
<text x="330" y="70" class="t" text-anchor="middle">Discipline</text>
<text x="250" y="96" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="250" y="114" class="f">title: str</text>
<text x="250" y="132" class="f">description: str</text>
<text x="250" y="150" class="f"><tspan class="fk">FK</tspan> teacher_id: int</text>

<!-- Lecture -->
<rect x="480" y="50" width="180" height="120" rx="8" class="box"/>
<rect x="480" y="50" width="180" height="28" rx="8" class="head"/>
<text x="570" y="70" class="t" text-anchor="middle">Lecture</text>
<text x="490" y="96" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="490" y="114" class="f">title: str</text>
<text x="490" y="132" class="f">body_text: text</text>
<text x="490" y="150" class="f"><tspan class="fk">FK</tspan> discipline_id: int</text>

<!-- Test -->
<rect x="710" y="50" width="180" height="130" rx="8" class="box"/>
<rect x="710" y="50" width="180" height="28" rx="8" class="head"/>
<text x="800" y="70" class="t" text-anchor="middle">Test</text>
<text x="720" y="96" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="720" y="114" class="f">title: str</text>
<text x="720" y="132" class="f">is_published: bool</text>
<text x="720" y="150" class="f">qr_token: str</text>
<text x="720" y="168" class="f"><tspan class="fk">FK</tspan> lecture_id: int</text>

<!-- Question -->
<rect x="710" y="220" width="180" height="130" rx="8" class="box"/>
<rect x="710" y="220" width="180" height="28" rx="8" class="head"/>
<text x="800" y="240" class="t" text-anchor="middle">Question</text>
<text x="720" y="266" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="720" y="284" class="f">text: str</text>
<text x="720" y="302" class="f">options: json</text>
<text x="720" y="320" class="f">correct_index: int</text>
<text x="720" y="338" class="f"><tspan class="fk">FK</tspan> test_id: int</text>

<!-- Attempt -->
<rect x="480" y="380" width="180" height="130" rx="8" class="box"/>
<rect x="480" y="380" width="180" height="28" rx="8" class="head"/>
<text x="570" y="400" class="t" text-anchor="middle">Attempt</text>
<text x="490" y="426" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="490" y="444" class="f"><tspan class="fk">FK</tspan> user_id: int</text>
<text x="490" y="462" class="f"><tspan class="fk">FK</tspan> test_id: int</text>
<text x="490" y="480" class="f">score: float</text>
<text x="490" y="498" class="f">created_at: datetime</text>

<!-- Answer -->
<rect x="710" y="400" width="180" height="120" rx="8" class="box"/>
<rect x="710" y="400" width="180" height="28" rx="8" class="head"/>
<text x="800" y="420" class="t" text-anchor="middle">Answer</text>
<text x="720" y="446" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="720" y="464" class="f"><tspan class="fk">FK</tspan> attempt_id: int</text>
<text x="720" y="482" class="f"><tspan class="fk">FK</tspan> question_id: int</text>
<text x="720" y="500" class="f">selected_index: int</text>

<!-- Teaching Assignment -->
<rect x="240" y="250" width="180" height="110" rx="8" class="box"/>
<rect x="240" y="250" width="180" height="28" rx="8" class="head"/>
<text x="330" y="270" class="t" text-anchor="middle">TeachingAssignment</text>
<text x="250" y="296" class="f"><tspan class="pk">PK</tspan> id: int</text>
<text x="250" y="314" class="f"><tspan class="fk">FK</tspan> teacher_id: int</text>
<text x="250" y="332" class="f"><tspan class="fk">FK</tspan> discipline_id: int</text>
<text x="250" y="350" class="f"><tspan class="fk">FK</tspan> group_id: int</text>

<!-- Relations -->
<line x1="180" y1="110" x2="240" y2="110" class="link"/>
<line x1="420" y1="110" x2="480" y2="110" class="link"/>
<line x1="660" y1="110" x2="710" y2="110" class="link"/>
<line x1="800" y1="180" x2="800" y2="220" class="link"/>
<line x1="660" y1="150" x2="710" y2="460" class="link"/>
<line x1="570" y1="170" x2="570" y2="380" class="link"/>
<line x1="95" y1="180" x2="95" y2="220" class="link"/>
<line x1="180" y1="300" x2="240" y2="300" class="link"/>
<line x1="330" y1="170" x2="330" y2="250" class="link"/>
<line x1="180" y1="150" x2="240" y2="300" class="link"/>
</svg>
""")

# ── 3-4. Sequence + Activity (reuse the presentation's inline SVGs) ──
# Extract from the presentation HTML
SEQUENCE_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 400" style="background:#fff">
<style>text{font-family:'Segoe UI',sans-serif}</style>
<text x="270" y="20" text-anchor="middle" font-weight="800" font-size="16" fill="#18304f">Диаграмма последовательностей</text>
<defs><marker id="sqF" viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="7" markerHeight="5" orient="auto"><path d="M0 0L10 3.5 0 7z" fill="#d77463"/></marker><marker id="sqB" viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="7" markerHeight="5" orient="auto"><path d="M0 0L10 3.5 0 7z" fill="#6f88c9"/></marker></defs>
<rect x="2" y="30" width="86" height="30" rx="10" fill="#eaf1fb" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="45" y="50" text-anchor="middle" font-weight="700" font-size="10" fill="#18304f">Преподаватель</text>
<rect x="112" y="30" width="76" height="30" rx="10" fill="#eaf1fb" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="150" y="50" text-anchor="middle" font-weight="700" font-size="10" fill="#18304f">Веб-сервис</text>
<rect x="218" y="30" width="76" height="30" rx="10" fill="#eaf1fb" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="256" y="50" text-anchor="middle" font-weight="700" font-size="10" fill="#18304f">AI-модуль</text>
<rect x="330" y="30" width="60" height="30" rx="10" fill="#eaf1fb" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="360" y="50" text-anchor="middle" font-weight="700" font-size="10" fill="#18304f">СУБД</text>
<rect x="430" y="30" width="76" height="30" rx="10" fill="#eaf1fb" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="468" y="50" text-anchor="middle" font-weight="700" font-size="10" fill="#18304f">Студент</text>
<line x1="45" y1="60" x2="45" y2="390" stroke="#8ea6c7" stroke-width="1" stroke-dasharray="5 3"/>
<line x1="150" y1="60" x2="150" y2="390" stroke="#8ea6c7" stroke-width="1" stroke-dasharray="5 3"/>
<line x1="256" y1="60" x2="256" y2="390" stroke="#8ea6c7" stroke-width="1" stroke-dasharray="5 3"/>
<line x1="360" y1="60" x2="360" y2="390" stroke="#8ea6c7" stroke-width="1" stroke-dasharray="5 3"/>
<line x1="468" y1="60" x2="468" y2="390" stroke="#8ea6c7" stroke-width="1" stroke-dasharray="5 3"/>
<rect x="42" y="80" width="6" height="42" rx="2" fill="#d9e2f1"/>
<line x1="48" y1="92" x2="148" y2="92" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="74" y="86" font-weight="500" font-size="9" fill="#4a6180">Создать лекцию</text>
<rect x="147" y="120" width="6" height="42" rx="2" fill="#d9e2f1"/>
<line x1="153" y1="132" x2="254" y2="132" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="170" y="126" font-weight="500" font-size="9" fill="#4a6180">Сгенерировать тест</text>
<line x1="254" y1="152" x2="153" y2="152" stroke="#6f88c9" stroke-width="1.6" stroke-dasharray="6 3" marker-end="url(#sqB)"/>
<text x="176" y="148" font-weight="500" font-size="9" fill="#4a6180">Набор вопросов</text>
<rect x="147" y="180" width="6" height="42" rx="2" fill="#d9e2f1"/>
<line x1="153" y1="192" x2="358" y2="192" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="218" y="186" font-weight="500" font-size="9" fill="#4a6180">Сохранить тест</text>
<line x1="48" y1="242" x2="148" y2="242" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="66" y="236" font-weight="500" font-size="9" fill="#4a6180">Опубликовать</text>
<rect x="147" y="268" width="6" height="42" rx="2" fill="#d9e2f1"/>
<line x1="466" y1="282" x2="153" y2="282" stroke="#6f88c9" stroke-width="1.6" marker-end="url(#sqB)"/>
<text x="280" y="276" font-weight="500" font-size="9" fill="#4a6180">Пройти тест</text>
<line x1="153" y1="300" x2="358" y2="300" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="220" y="294" font-weight="500" font-size="9" fill="#4a6180">Записать результат</text>
<rect x="147" y="330" width="6" height="42" rx="2" fill="#d9e2f1"/>
<line x1="153" y1="346" x2="466" y2="346" stroke="#d77463" stroke-width="1.6" marker-end="url(#sqF)"/>
<text x="275" y="340" font-weight="500" font-size="9" fill="#4a6180">Результат и аналитика</text>
<line x1="153" y1="366" x2="48" y2="366" stroke="#6f88c9" stroke-width="1.6" stroke-dasharray="6 3" marker-end="url(#sqB)"/>
<text x="72" y="360" font-weight="500" font-size="9" fill="#4a6180">Аналитика преподавателя</text>
</svg>"""

ACTIVITY_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 540 400" style="background:#fff">
<style>text{font-family:'Segoe UI',sans-serif}</style>
<text x="270" y="20" text-anchor="middle" font-weight="800" font-size="16" fill="#18304f">Диаграмма активности</text>
<defs><marker id="adA" viewBox="0 0 10 7" refX="9" refY="3.5" markerWidth="7" markerHeight="5" orient="auto"><path d="M0 0L10 3.5 0 7z" fill="#8ea6c7"/></marker></defs>
<circle cx="270" cy="38" r="7" fill="#18304f"/>
<line x1="270" y1="45" x2="270" y2="58" stroke="#8ea6c7" stroke-width="1.5" marker-end="url(#adA)"/>
<rect x="195" y="60" width="150" height="28" rx="14" fill="#fff" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="270" y="78" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Вход в систему</text>
<line x1="270" y1="88" x2="270" y2="100" stroke="#8ea6c7" stroke-width="1.5" marker-end="url(#adA)"/>
<rect x="190" y="102" width="160" height="28" rx="14" fill="#fff" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="270" y="120" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Выбор дисциплины</text>
<line x1="270" y1="130" x2="270" y2="142" stroke="#8ea6c7" stroke-width="1.5" marker-end="url(#adA)"/>
<rect x="195" y="144" width="150" height="28" rx="14" fill="#fff" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="270" y="162" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Создание лекции</text>
<line x1="270" y1="172" x2="270" y2="186" stroke="#8ea6c7" stroke-width="1.5"/>
<polygon points="270,186 302,206 270,226 238,206" fill="#fff" stroke="#d77463" stroke-width="2"/>
<text x="270" y="210" text-anchor="middle" font-weight="600" font-size="9" fill="#6f88c9">Режим?</text>
<line x1="238" y1="206" x2="130" y2="206" stroke="#d77463" stroke-width="1.5"/>
<text x="160" y="200" text-anchor="middle" font-weight="500" font-size="8" fill="#d77463">AI</text>
<line x1="130" y1="206" x2="130" y2="232" stroke="#d77463" stroke-width="1.5"/>
<rect x="55" y="232" width="150" height="28" rx="14" fill="#fff" stroke="#d77463" stroke-width="1.5"/>
<text x="130" y="250" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">AI-генерация</text>
<line x1="130" y1="260" x2="130" y2="278" stroke="#d77463" stroke-width="1.5"/>
<line x1="130" y1="278" x2="268" y2="278" stroke="#d77463" stroke-width="1.5"/>
<line x1="302" y1="206" x2="410" y2="206" stroke="#6f88c9" stroke-width="1.5"/>
<text x="380" y="200" text-anchor="middle" font-weight="500" font-size="8" fill="#6f88c9">Вручную</text>
<line x1="410" y1="206" x2="410" y2="232" stroke="#6f88c9" stroke-width="1.5"/>
<rect x="335" y="232" width="150" height="28" rx="14" fill="#fff" stroke="#6f88c9" stroke-width="1.5"/>
<text x="410" y="250" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Ручной конструктор</text>
<line x1="410" y1="260" x2="410" y2="278" stroke="#6f88c9" stroke-width="1.5"/>
<line x1="410" y1="278" x2="272" y2="278" stroke="#6f88c9" stroke-width="1.5"/>
<line x1="270" y1="278" x2="270" y2="294" stroke="#8ea6c7" stroke-width="1.5" marker-end="url(#adA)"/>
<rect x="175" y="296" width="190" height="28" rx="14" fill="#fff" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="270" y="314" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Редактирование и публикация</text>
<line x1="270" y1="324" x2="270" y2="340" stroke="#8ea6c7" stroke-width="1.5" marker-end="url(#adA)"/>
<rect x="195" y="342" width="150" height="28" rx="14" fill="#fff" stroke="#8ea6c7" stroke-width="1.5"/>
<text x="270" y="360" text-anchor="middle" font-weight="600" font-size="10" fill="#18304f">Анализ результатов</text>
<line x1="270" y1="370" x2="270" y2="384" stroke="#8ea6c7" stroke-width="1.5"/>
<circle cx="270" cy="390" r="5" fill="none" stroke="#18304f" stroke-width="2"/>
<circle cx="270" cy="390" r="3" fill="#18304f"/>
</svg>"""

# ── 5. ER diagram (simplified from presentation) ─────────────
ER_SVG = """\
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1000 650" style="background:#fff">
<style>
  text{font-family:'Segoe UI',sans-serif}
  .box{fill:#fff;stroke:#8ea6c7;stroke-width:2}
  .head{fill:#eaf1fb}
  .t{font:700 13px 'Segoe UI';fill:#18304f}
  .f{font:11px 'Segoe UI';fill:#4a6180}
  .pk{fill:#d77463;font:700 9px 'Segoe UI'}
  .fk{fill:#6f88c9;font:700 9px 'Segoe UI'}
  .link{stroke:#7b90b0;stroke-width:1.8;fill:none}
  .title{fill:#18304f;font:800 16px 'Segoe UI';text-anchor:middle}
</style>
<text x="500" y="24" class="title">Физическая модель базы данных — КампусПлюс</text>

<!-- users -->
<rect x="10" y="40" width="160" height="150" rx="6" class="box"/>
<rect x="10" y="40" width="160" height="24" rx="6" class="head"/>
<text x="90" y="57" class="t" text-anchor="middle">users</text>
<text x="18" y="78" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="18" y="94" class="f">full_name TEXT</text>
<text x="18" y="110" class="f">login TEXT UNIQUE</text>
<text x="18" y="126" class="f">password_hash TEXT</text>
<text x="18" y="142" class="f">role TEXT</text>
<text x="18" y="158" class="f"><tspan class="fk">FK</tspan> group_id INTEGER</text>
<text x="18" y="174" class="f">created_at TIMESTAMP</text>

<!-- groups -->
<rect x="10" y="230" width="160" height="100" rx="6" class="box"/>
<rect x="10" y="230" width="160" height="24" rx="6" class="head"/>
<text x="90" y="247" class="t" text-anchor="middle">groups</text>
<text x="18" y="268" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="18" y="284" class="f">name TEXT</text>
<text x="18" y="300" class="f">year INTEGER</text>
<text x="18" y="316" class="f">created_at TIMESTAMP</text>

<!-- disciplines -->
<rect x="220" y="40" width="180" height="120" rx="6" class="box"/>
<rect x="220" y="40" width="180" height="24" rx="6" class="head"/>
<text x="310" y="57" class="t" text-anchor="middle">disciplines</text>
<text x="228" y="78" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="228" y="94" class="f">title TEXT</text>
<text x="228" y="110" class="f">description TEXT</text>
<text x="228" y="126" class="f"><tspan class="fk">FK</tspan> teacher_id INTEGER</text>
<text x="228" y="142" class="f">created_at TIMESTAMP</text>

<!-- teaching_assignments -->
<rect x="220" y="200" width="180" height="120" rx="6" class="box"/>
<rect x="220" y="200" width="180" height="24" rx="6" class="head"/>
<text x="310" y="217" class="t" text-anchor="middle">teaching_assignments</text>
<text x="228" y="238" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="228" y="254" class="f"><tspan class="fk">FK</tspan> teacher_id INTEGER</text>
<text x="228" y="270" class="f"><tspan class="fk">FK</tspan> discipline_id INTEGER</text>
<text x="228" y="286" class="f"><tspan class="fk">FK</tspan> group_id INTEGER</text>
<text x="228" y="302" class="f">created_at TIMESTAMP</text>

<!-- lectures -->
<rect x="460" y="40" width="180" height="120" rx="6" class="box"/>
<rect x="460" y="40" width="180" height="24" rx="6" class="head"/>
<text x="550" y="57" class="t" text-anchor="middle">lectures</text>
<text x="468" y="78" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="468" y="94" class="f">title TEXT</text>
<text x="468" y="110" class="f">body_text TEXT</text>
<text x="468" y="126" class="f"><tspan class="fk">FK</tspan> discipline_id INTEGER</text>
<text x="468" y="142" class="f">created_at TIMESTAMP</text>

<!-- tests -->
<rect x="700" y="40" width="170" height="140" rx="6" class="box"/>
<rect x="700" y="40" width="170" height="24" rx="6" class="head"/>
<text x="785" y="57" class="t" text-anchor="middle">tests</text>
<text x="708" y="78" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="708" y="94" class="f">title TEXT</text>
<text x="708" y="110" class="f"><tspan class="fk">FK</tspan> lecture_id INTEGER</text>
<text x="708" y="126" class="f">is_published BOOLEAN</text>
<text x="708" y="142" class="f">qr_token TEXT</text>
<text x="708" y="158" class="f">created_at TIMESTAMP</text>

<!-- questions -->
<rect x="700" y="220" width="170" height="130" rx="6" class="box"/>
<rect x="700" y="220" width="170" height="24" rx="6" class="head"/>
<text x="785" y="237" class="t" text-anchor="middle">questions</text>
<text x="708" y="258" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="708" y="274" class="f">text TEXT</text>
<text x="708" y="290" class="f">options JSON</text>
<text x="708" y="306" class="f">correct_index INTEGER</text>
<text x="708" y="322" class="f"><tspan class="fk">FK</tspan> test_id INTEGER</text>
<text x="708" y="338" class="f">created_at TIMESTAMP</text>

<!-- attempts -->
<rect x="460" y="380" width="180" height="130" rx="6" class="box"/>
<rect x="460" y="380" width="180" height="24" rx="6" class="head"/>
<text x="550" y="397" class="t" text-anchor="middle">attempts</text>
<text x="468" y="418" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="468" y="434" class="f"><tspan class="fk">FK</tspan> user_id INTEGER</text>
<text x="468" y="450" class="f"><tspan class="fk">FK</tspan> test_id INTEGER</text>
<text x="468" y="466" class="f">score REAL</text>
<text x="468" y="482" class="f">created_at TIMESTAMP</text>
<text x="468" y="498" class="f">finished_at TIMESTAMP</text>

<!-- answers -->
<rect x="700" y="400" width="170" height="120" rx="6" class="box"/>
<rect x="700" y="400" width="170" height="24" rx="6" class="head"/>
<text x="785" y="417" class="t" text-anchor="middle">answers</text>
<text x="708" y="438" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="708" y="454" class="f"><tspan class="fk">FK</tspan> attempt_id INTEGER</text>
<text x="708" y="470" class="f"><tspan class="fk">FK</tspan> question_id INTEGER</text>
<text x="708" y="486" class="f">selected_index INTEGER</text>
<text x="708" y="502" class="f">is_correct BOOLEAN</text>

<!-- teaching_assignment_blocks -->
<rect x="220" y="370" width="180" height="100" rx="6" class="box"/>
<rect x="220" y="370" width="180" height="24" rx="6" class="head"/>
<text x="310" y="387" class="t" text-anchor="middle">assignment_blocks</text>
<text x="228" y="408" class="f"><tspan class="pk">PK</tspan> id INTEGER</text>
<text x="228" y="424" class="f"><tspan class="fk">FK</tspan> assignment_id INTEGER</text>
<text x="228" y="440" class="f">title TEXT</text>
<text x="228" y="456" class="f">weight REAL</text>

<!-- Relations -->
<line x1="90" y1="190" x2="90" y2="230" class="link"/>
<line x1="170" y1="100" x2="220" y2="100" class="link"/>
<line x1="400" y1="100" x2="460" y2="100" class="link"/>
<line x1="640" y1="100" x2="700" y2="100" class="link"/>
<line x1="785" y1="180" x2="785" y2="220" class="link"/>
<line x1="785" y1="350" x2="785" y2="400" class="link"/>
<line x1="640" y1="440" x2="700" y2="440" class="link"/>
<line x1="170" y1="270" x2="220" y2="270" class="link"/>
<line x1="310" y1="160" x2="310" y2="200" class="link"/>
<line x1="310" y1="320" x2="310" y2="370" class="link"/>
<line x1="550" y1="160" x2="550" y2="380" class="link"/>
</svg>"""

# ── Render each SVG to PNG ────────────────────────────────────
def render_svgs():
    from playwright.sync_api import sync_playwright

    diagrams = [
        ("use_case.png", USE_CASE_SVG, 820, 520),
        ("class_diagram.png", CLASS_SVG, 900, 600),
        ("sequence.png", SEQUENCE_SVG, 540, 400),
        ("activity.png", ACTIVITY_SVG, 540, 400),
        ("er_diagram.png", ER_SVG, 1000, 650),
    ]

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, svg, w, h in diagrams:
            page = browser.new_page(viewport={"width": w * 2, "height": h * 2})
            html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>body{{margin:0;background:#fff;display:flex;align-items:center;justify-content:center;min-height:100vh}}</style>
</head><body>{svg}</body></html>"""
            page.set_content(html, wait_until="networkidle")
            out_path = OUT_DIR / name
            page.screenshot(path=str(out_path), full_page=True)
            page.close()
            print(f"  ✓ {name} ({out_path.stat().st_size // 1024} KB)")

        browser.close()

# ── Generate QR code ──────────────────────────────────────────
def generate_qr():
    try:
        import qrcode
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "qrcode[pil]", "-q"])
        import qrcode

    qr = qrcode.QRCode(version=1, box_size=20, border=2,
                        error_correction=qrcode.constants.ERROR_CORRECT_H)
    qr.add_data(VKR_URL)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    out = OUT_DIR / "qr_vkr_materials.png"
    img.save(str(out))
    print(f"  ✓ QR: {out} ({out.stat().st_size // 1024} KB)")
    print(f"    URL: {VKR_URL}")


if __name__ == "__main__":
    print("Rendering UML diagrams...")
    render_svgs()
    print("\nGenerating QR code...")
    generate_qr()
    print("\nDone!")
