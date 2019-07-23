from example_dataclass import Example
import pytest


def test_type_extension_not_visible():
    e = Example()
    with pytest.raises(AttributeError):
        e.foo()
    with pytest.raises(AttributeError):
        e.one_plus(1)