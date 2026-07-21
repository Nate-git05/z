from .settings import Config


def load_port():
    c = Config({"port": 8080})
    return c.get_value("port")


def load_host():
    c = Config({"host": "localhost"})
    return c.get_value("host", "127.0.0.1")
