"""Add examples only to manually entered values, preserving defaults and choice help."""
from django import forms


def apply_field_examples(form, examples):
    for name, (example, explanation) in examples.items():
        if name not in form.fields:
            continue
        field = form.fields[name]
        if field.widget.is_hidden or isinstance(field.widget, (
            forms.Select, forms.RadioSelect, forms.CheckboxInput,
            forms.CheckboxSelectMultiple, forms.FileInput,
        )):
            continue
        field.help_text = ' '.join(filter(None, (field.help_text, f'示例：{example}。{explanation}')))
        # Dynamic inspection rows may already hold a BoundField with copied help text.
        form[name].help_text = field.help_text
        field.widget.attrs.setdefault('placeholder', example)
