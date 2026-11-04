"""频道分类：按 config 的正则规则把频道名映射到分类。"""
import re


def classify(name: str, rules: dict) -> str:
    for cat, patterns in rules.items():
        if not isinstance(patterns, list):
            continue
        for pat in patterns:
            if pat and isinstance(pat, str) and re.search(pat, name, re.I):
                return cat
    return list(rules)[-1] if rules else "其他频道"
