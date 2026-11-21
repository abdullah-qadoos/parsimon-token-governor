import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shop import cart_total, format_price  # noqa: E402


def test_format_price():
    assert format_price(1999) == "$19.99"


def test_cart_total():
    assert cart_total([{"price": 500, "qty": 2}, {"price": 250, "qty": 1}]) == 1250
