import pytest

from pantry_chef.llm.prompt_loader import load_prompt, parse_prompt_file


def test_parse_prompt_file_reads_header_and_body():
    prompt = parse_prompt_file("---\nname: demo\nversion: 2\n---\nHello $who {not a var}")
    assert (prompt.name, prompt.version) == ("demo", "2")
    assert prompt.render(who="chef") == "Hello chef {not a var}"


def test_render_fails_when_a_variable_is_missing():
    prompt = parse_prompt_file("---\nname: demo\nversion: 1\n---\nHello $who")
    with pytest.raises(KeyError):
        prompt.render()


def test_ingredient_labeling_prompt_is_versioned_and_lists_all_allergens():
    prompt = load_prompt("ingredient_labeling")
    assert prompt.version == "2"
    text = prompt.render(ingredients='["butter"]')
    for code in ("gluten", "crustaceans", "tree_nuts", "sulphites", "lupin", "molluscs"):
        assert code in text
    assert '["butter"]' in text
    assert "quantity_matters" not in text  # has its own prompt since v2


def test_quantity_matters_prompt_follows_the_plan_examples():
    text = load_prompt("quantity_matters").render(ingredients="[]")
    true_part, false_part = text.split("quantity_matters = false")
    assert "eggs" in true_part and "pasta" in true_part
    for basic in ("flour", "milk", "bread", "salt"):
        assert basic in false_part


def test_prompts_are_not_sensitive_unless_their_header_says_so():
    assert not parse_prompt_file("---\nname: a\nversion: 1\n---\nx").sensitive
    assert parse_prompt_file("---\nname: a\nversion: 1\nsensitive: true\n---\nx").sensitive
