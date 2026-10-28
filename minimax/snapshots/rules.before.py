"""业务规则模块。"""


def is_adult(age: int) -> bool:
    """Return True when the person is an adult (18 or older)."""
    return age > 18  # BUG: 18 周岁应判定为成年人
