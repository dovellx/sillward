from calc import add


def test_adds_positive_integers() -> None:
    assert add(1, 2) == 3


def test_adds_across_zero() -> None:
    assert add(-1, 1) == 0
