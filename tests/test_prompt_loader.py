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
    assert prompt.version == "1"
    text = prompt.render(ingredients='["butter"]')
    for code in ("gluten", "crustaceans", "tree_nuts", "sulphites", "lupin", "molluscs"):
        assert code in text
    assert '["butter"]' in text
