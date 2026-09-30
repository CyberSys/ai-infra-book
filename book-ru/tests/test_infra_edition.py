from scripts.check_translation import BOOK_TITLE, EXPECTED_FILES, TRANSLATION_NOTICE


def test_infra_title_and_attribution() -> None:
    assert BOOK_TITLE == "AI-инфраструктура изнутри: количественный анализ и проектирование систем"
    assert "https://github.com/bojieli/ai-infra-book;" in TRANSLATION_NOTICE


def test_infra_inventory_contains_preface_and_twelve_chapters() -> None:
    assert EXPECTED_FILES == ("preface.md", *(f"chapter{i}.md" for i in range(1, 13)))
