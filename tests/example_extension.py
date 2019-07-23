from type_extensions import extension
from example_dataclass import Example


@extension
def foo(self: Example) -> bool:
    return True


@extension
def one_plus(self: Example, arg: int) -> int:
    return arg + 1