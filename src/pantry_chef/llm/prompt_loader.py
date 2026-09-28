"""Load versioned prompt files from llm/prompts/.

Each file starts with a small header (name, version, optional `sensitive: true`) so every
LLM call can be traced back to the exact prompt that produced it. Sensitive prompts carry
the user's health answers; their text is masked in traces. Variables use $name
(string.Template), so braces in the prompt text never need escaping.
"""

from dataclasses import dataclass
from importlib.resources import files
from string import Template


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    template: str
    sensitive: bool = False  # input/output hidden in traces (health information)

    def render(self, **variables: str) -> str:
        return Template(self.template).substitute(variables)


def parse_prompt_file(text: str) -> Prompt:
    """Split "---\\nname: x\\nversion: 1\\n---\\n<body>" into a Prompt."""
    _, header, body = text.split("---\n", 2)
    fields = dict(line.split(":", 1) for line in header.strip().splitlines())
    return Prompt(
        name=fields["name"].strip(),
        version=fields["version"].strip(),
        template=body.strip(),
        sensitive=fields.get("sensitive", "false").strip().lower() == "true",
    )


def load_prompt(name: str) -> Prompt:
    text = files("pantry_chef.llm.prompts").joinpath(f"{name}.md").read_text()
    prompt = parse_prompt_file(text)
    if prompt.name != name:
        raise ValueError(f"prompt file {name}.md declares name {prompt.name!r}")
    return prompt
