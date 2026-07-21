"""Inline bubble sort — refactor should extract swap helper without behavior change."""


def sort_list(items):
    xs = list(items)
    n = len(xs)
    for i in range(n):
        for j in range(0, n - i - 1):
            if xs[j] > xs[j + 1]:
                tmp = xs[j]
                xs[j] = xs[j + 1]
                xs[j + 1] = tmp
    return xs
