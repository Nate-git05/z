"""Inventory stock tracker."""


class Stock:
    def __init__(self, sku: str, qty: int = 0):
        self.sku = sku
        self.qty = qty

    def receive(self, n: int) -> None:
        if n < 0:
            raise ValueError("n must be >= 0")
        self.qty += n

    def ship(self, n: int) -> None:
        """Ship n units.

        BUG: allows shipping one more than available (uses > instead of >=).
        """
        if n < 0:
            raise ValueError("n must be >= 0")
        if n > self.qty + 1:  # buggy threshold
            raise ValueError("insufficient stock")
        self.qty -= n
