from collections import UserDict
from collections.abc import MutableMapping
from dataclasses import dataclass
from importlib import import_module
from inspect import signature, stack, Parameter
from pathlib import Path
from pprint import pprint
from types import FunctionType, MethodType
import functools
import inspect
import pkgutil
import sys


def get_calling_frame():
    candidate_frame = inspect.currentframe().f_back.f_back
    if candidate_frame.f_globals["__name__"] == __name__:
        candidate_frame = candidate_frame.f_back
    return candidate_frame


def first_parm_of(f, except_if_none=None):
    sig = signature(f)
    first_parm = next(iter(sig.parameters.values()))
    if first_parm is None and except_if_none is not None:
        raise Exception()
    return first_parm


def extension(f):
    """
    Transform a function into a type extension
    """
    self_parm = first_parm_of(
        f, "A function with no parameters can't be used as a type extension"
    )
    if self_parm.annotation is Parameter.empty:
        raise Exception(
            "A type extension function must include a type annotation for the first parameter"
        )
    target_type = self_parm.annotation
    calling_frame = get_calling_frame()
    calling_module = calling_frame.f_globals["__name__"]
    if ExtendableType not in target_type.__bases__:
        target_type = replace_with_extendable_type(target_type, calling_module)
    target_type.__scoped_setattr__(calling_module, f.__name__, f)
    return f


def mextension(f):
    """
    *Monadic* extension of a class. Returns a function that has been
    added as a member of the class of the first parameter, `self`, adds
    an annotation for the signature with that same class as the return type,
    and finally returns the instance
    """

    @functools.wraps(f)
    def wrapper(self, *args, **kwargs):
        f(self, *args, **kwargs)
        return self

    wrapper.__annotations__ = dict(f.__annotations__)
    if "returns" not in wrapper.__annotations__:
        first_parm = first_parm_of(
            f, "A function with no parameters can't be used as a type extension"
        )
        wrapper.__annotations__["returns"] = first_parm
    return extension(wrapper)


"""
TODO define an approach to module-scoped extension visibility by overriding
__dict__ in extended classes. 

On initial import:
* replace __dict__ with a wrapper

On access to extension method:
* look in the cache, if noth known, then
* determine importing module by walking the stack and inspecting imports, looking
  for references to the module that defines the extension
* cache the fact that the importing module can see the extension methods or not
* If the accessing module (caller up on call stack?) imported the
  extension respond with the extension. If not, cache that fact and call into
  the wrapped __dict__ for response.
"""


class ModuleScopingDict(UserDict):
    def get_or_create_scoped_item_dict_for_module(self, module_name):
        if module_name not in self.data:
            self.data[module_name] = dict()
        return self.data[module_name]

    def scoped_setitem(self, module_name, key, value):
        self.get_or_create_scoped_item_dict_for_module(module_name)[key] = value
        
    def scoped_getitem(self, module_name, key):
        return self.data[module_name][key]


class NameGenerator:
    def __init__(self, base_name):
        self.base_name = base_name
        self.suffix = 0

    def __call__(self):
        self.suffix += 1
        yield f"{self.base_name}_{self.suffix}"


class ExtendableType:
    __scoped_attrs__ = ModuleScopingDict()
        
    def __getattr__(self, attr):
        module = get_calling_frame().f_globals["__name__"]
        if module not in self.__scoped_attrs__ or attr not in self.__scoped_attrs__[module]:
            print(f"scope: {module}  instance: {self}  attr: {attr}")
            print(self.__scoped_attrs__)
            raise AttributeError()
        resolved_attr = self.__scoped_attrs__.scoped_getitem(module, attr)
        if isinstance(resolved_attr, FunctionType):
            resolved_attr = MethodType(resolved_attr, self)
            setattr(self, attr, resolved_attr)
        return resolved_attr

    @classmethod
    def __scoped_setattr__(cls, module, attr, value):
        cls.__scoped_attrs__.scoped_setitem(module, attr, value)
        

def replace_with_extendable_type(target_type, calling_module):
    # generate a safe name for the replacement type
    generate_name = NameGenerator(target_type.__name__)()
    classname = next(generate_name)
    while hasattr(target_type.__module__, classname):
        classname = generate_name()
    scoping_dict = ModuleScopingDict()
    extendable_type = type(
        classname, (ExtendableType, target_type), dict()
    )
    # replace the target_type with the new extendable_type in the originating module
    # FIXME could we just add the ExtendableType as ... metaclass? or something?
    target_module = import_module(target_type.__module__)
    setattr(target_module, target_type.__name__, extendable_type)
    # replace the target_type with the new extendable type in the calling module, too, if
    # needed
    target_module = import_module(calling_module)
    if hasattr(target_module, target_type.__name__) and getattr(target_module, target_type.__name__) is target_type:
        setattr(target_module, target_type.__name__, extendable_type)
    return extendable_type