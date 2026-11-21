"""A tiny shop module used as a benchmark target."""


def format_price(cents):
    return f"${cents / 100:.2f}"


def cart_total(items):
    total = 0
    for item in items:
        total += item["price"] * item["qty"]
    return total


def apply_discount(total, percent):
    return total
