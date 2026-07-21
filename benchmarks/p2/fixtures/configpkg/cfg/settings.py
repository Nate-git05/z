"""Config API — migration renames get_value → get."""


class Config:
    def __init__(self, data=None):
        self._data = dict(data or {})

    def get_value(self, key, default=None):
        """Legacy API — callers must migrate to get()."""
        return self._data.get(key, default)

    def set_value(self, key, value):
        self._data[key] = value
