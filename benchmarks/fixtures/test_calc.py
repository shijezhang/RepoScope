from api import old_order, order, plugin


def test_positive():
    assert order(10) == 10


def test_negative():
    assert order(-2) == 0


def test_legacy():
    assert old_order(10) == 10


def test_plugin():
    assert plugin(10) == 10
