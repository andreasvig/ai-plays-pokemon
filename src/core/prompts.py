"""Prompt template substitution.

Replaces {{key}} placeholders in prompt strings with runtime values.
Uses double-brace syntax to avoid conflicts with JSON examples in prompts.
"""

from typing import Any


def fill_prompt(template: str, **kwargs: Any) -> str:
    """Replace {{key}} placeholders in a template string.

    Args:
        template: String with {{key}} placeholders.
        **kwargs: Key-value pairs to substitute.

    Returns:
        The template with all matching placeholders replaced.
        Unmatched placeholders are left as-is.
    """
    for key, value in kwargs.items():
        template = template.replace(f"{{{{{key}}}}}", str(value))
    return template
