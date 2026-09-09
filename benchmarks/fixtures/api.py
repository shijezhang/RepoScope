from calc import Base, legacy, total


def order(value):
    return total(value)


def old_order(value):
    return legacy(value)


def plugin(value):
    import calc

    return getattr(calc, "total")(value)


class Special(Base):
    def price(self, value):
        return super().price(value)
