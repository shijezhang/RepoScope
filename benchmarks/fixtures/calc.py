def total(amount):
    return amount if amount >= 0 else 0


def legacy(value):
    return total(value)


class Base:
    def price(self, value):
        return total(value)
