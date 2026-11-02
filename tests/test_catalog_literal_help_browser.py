from uuid import uuid4

import pytest
from playwright.sync_api import expect

from test_admin_catalog_navigation_browser import create_account
from test_ui_contextual_help_browser import assert_described, assert_help_is_text
from test_web_ui_admin_browser import enter, section
from test_web_ui_browser import browser_page


@pytest.mark.parametrize("literal", ["%", "_", "\\"])
def test_catalog_help_matches_literal_character_search(browser_page, scenario, literal):
    page, case = browser_page, scenario
    marker = uuid4().hex
    user = create_account(case, email=f"{marker}-literal@integration.invalid",
                          first_name=f"{marker}{literal}tail")
    create_account(case, email=f"{marker}-decoy@integration.invalid",
                   first_name=f"{marker}Xtail")
    create_account(case, email=f"{marker}-plain@integration.invalid",
                   first_name=f"{marker}tail")
    enter(page, case.actors["admin"]["email"])
    section(page, "Учетные записи")
    query = page.get_by_label("Поиск по email, имени или фамилии", exact=True)
    assert_described(page, query)
    description = page.locator(f'[id="{query.get_attribute("aria-describedby")}"]')
    expect(description).to_contain_text("Символы %, _ и обратная косая черта ищутся буквально")
    expect(description).to_be_visible()
    assert_help_is_text(page)

    query.fill(f"{marker}{literal}tail")
    query.press("Enter")
    expect(page.get_by_text("Всего: 1. Страница: 1. Размер: 10.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name=f"Открыть учетную запись {user['id']}", exact=True)).to_be_visible()
    expect(query).to_be_focused()
