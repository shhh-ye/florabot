"""
Тест механизма скиллов: файлы из skills/ попадают в системный промпт,
выключенные (.md.off) — нет, порядок алфавитный, пустая папка не ломает.

Запуск из корня проекта:  python tests/test_skills.py
Сеть и настоящие ключи не нужны.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("OPENAI_API_KEY", "sk-test-fake")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:fake")
os.environ.setdefault("TELEGRAM_WEBHOOK_SECRET", "test-secret")
os.environ["RAG_INDEX_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="rag-test-"), "index.json"
)

skills_dir = tempfile.mkdtemp(prefix="skills-test-")
os.environ["SKILLS_DIR"] = skills_dir

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from bot.prompts import build_system_prompt, load_skills

# Пустая папка — скиллов нет, промпт собирается без блока скиллов
p = build_system_prompt("факт из базы знаний")
assert "Правила поведения (скиллы)" not in p
assert "факт из базы знаний" in p
print("OK: пустая папка скиллов не ломает промпт")

# Два скилла + один выключенный
Path(skills_dir, "b-tone.md").write_text("ПРАВИЛО-ТОН: тестовое правило", encoding="utf-8")
Path(skills_dir, "a-first.md").write_text("ПРАВИЛО-ПЕРВОЕ: другое правило", encoding="utf-8")
Path(skills_dir, "c-disabled.md.off").write_text("ВЫКЛЮЧЕННОЕ ПРАВИЛО", encoding="utf-8")

skills = load_skills()
assert "ПРАВИЛО-ТОН" in skills and "ПРАВИЛО-ПЕРВОЕ" in skills
assert "ВЫКЛЮЧЕННОЕ ПРАВИЛО" not in skills
assert skills.index("ПРАВИЛО-ПЕРВОЕ") < skills.index("ПРАВИЛО-ТОН"), "порядок должен быть алфавитным"
print("OK: скиллы подхватываются по алфавиту, .md.off игнорируется")

p = build_system_prompt("контекст")
assert "Правила поведения (скиллы)" in p
assert "ПРАВИЛО-ТОН" in p and "ВЫКЛЮЧЕННОЕ ПРАВИЛО" not in p
assert p.index("=== Правила поведения (скиллы) ===") < p.index("=== База знаний ==="), (
    "раздел скиллов идёт до раздела базы знаний"
)
print("OK: скиллы попадают в системный промпт")

# Реальная папка проекта: стилевой скилл на месте и построен на humanizer
real = (ROOT / "skills" / "humanizer.md").read_text(encoding="utf-8")
assert "humanizer" in real.lower() and "ПЛОХО" in real and "ХОРОШО" in real
print("OK: skills/humanizer.md существует и содержит правила стиля")

print("\nВСЕ ПРОВЕРКИ ПРОЙДЕНЫ")
