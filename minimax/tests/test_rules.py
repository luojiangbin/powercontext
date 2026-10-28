"""功能与回归测试。"""

from app.rules import is_adult


def test_adult_is_recognized():
    """目标功能：18 周岁应为成年人。改前失败，改后通过。"""
    assert is_adult(18) is True


def test_minor_is_not_adult():
    """回归：未成年人应判定为非成年人。"""
    assert is_adult(17) is False


def test_negative_age_is_not_adult():
    """回归：负数年龄应判定为非成年人。"""
    assert is_adult(-1) is False
