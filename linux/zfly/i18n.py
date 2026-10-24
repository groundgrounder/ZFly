"""Application-local language selection and Chinese-source-string translation."""
import re
import os

from .translations import EN, EN_PLURALS

_ZH = {english: chinese for chinese, english in EN.items() if chinese != english}


def _source_template(text, template, source):
    names = re.findall(r"{(\w+)}", template)
    if not names:
        return None
    pattern = re.escape(template)
    for name in names:
        pattern = pattern.replace(re.escape("{" + name + "}"), f"(?P<{name}>.*?)")
    match = re.fullmatch(pattern, text)
    if not match:
        return None
    values = {name: source_text(value) for name, value in match.groupdict().items()}
    return source.format(**values)


def source_text(text):
    if text in EN:
        return text
    source = _ZH.get(text)
    if source is not None:
        return source
    for chinese, english in EN.items():
        source = _source_template(text, english, chinese)
        if source is not None:
            return source
    for (singular, plural), (english_singular, english_plural) in EN_PLURALS.items():
        for source, template in ((singular, english_singular), (plural, english_plural)):
            result = _source_template(text, template, source)
            if result is not None:
                return result
    return text


LANGUAGES = ("system", "zh-Hans", "en")
_language = "system"


def _effective_language(preference):
    if preference != "system":
        return preference
    for name in ("LANGUAGE", "LC_ALL", "LC_CTYPE", "LANG"):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        locale = value.split(":", 1)[0].split(".", 1)[0].replace("-", "_").lower()
        if locale in ("c", "posix"):
            continue
        return "zh-Hans" if locale == "zh" or locale.startswith("zh_") else "en"
    return "zh-Hans"


def set_language(value):
    if value not in LANGUAGES:
        raise ValueError(f"unsupported language: {value!r}")
    global _language
    _language = value


def get_language():
    return _language


def tr(text, **values):
    translated = EN.get(text, text) if _effective_language(_language) == "en" else text
    return translated.format(**values) if values else translated


def trn(singular, plural, count, **values):
    source = singular if count == 1 else plural
    translated = source
    if _effective_language(_language) == "en":
        translated = EN_PLURALS.get((singular, plural), (singular, plural))[0 if count == 1 else 1]
    return translated.format(count=count, **values)
