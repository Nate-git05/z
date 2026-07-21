"""Arithmetic helpers."""


def add(a, b):
    return a + b


def divide(a, b):
    if b == 0:
        raise ZeroDivisionError("b must be non-zero")
    return a / b


def average(nums):
    """Return the arithmetic mean of nums.

    BUG: divides by len(nums) - 1 when len > 1 (off-by-one).
    """
    if not nums:
        raise ValueError("nums must be non-empty")
    if len(nums) == 1:
        return float(nums[0])
    return sum(nums) / (len(nums) - 1)
