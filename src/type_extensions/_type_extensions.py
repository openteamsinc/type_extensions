from collections import UserDict
from collections.abc import MutableMapping
from dataclasses import dataclass
from importlib import import_module
from importlib.abc import MetaPathFinder, Loader
from importlib.machinery import ModuleSpec
from inspect import currentframe, signature, stack, Parameter
from pathlib import Path
from types import FunctionType, MethodType
import functools
import inspect
import pkgutil
import sys


def is_not_candidate_frame_name(name, not_calling_frame):
    return name is __name__ or name in not_calling_frame


def get_calling_frame_as_import():
    candidate_frame = currentframe()
    while not candidate_frame.f_globals["__name__"].startswith("importlib._bootstrap"):
        if candidate_frame.f_back is None:
            return None
        else:
            candidate_frame = candidate_frame.f_back
    while candidate_frame.f_globals["__name__"].startswith("importlib._bootstrap"):
        candidate_frame = candidate_frame.f_back
    return candidate_frame


def get_calling_frame(not_calling_frame=[]):
    candidate_frame = currentframe()
    while is_not_candidate_frame_name(candidate_frame.f_globals["__name__"],
            not_calling_frame):
        candidate_frame = candidate_frame.f_back
    return candidate_frame


def first_parm_of(f, except_if_none=None):
    if isinstance(f, property):
        f = f.fget
    sig = signature(f)
    first_parm = next(iter(sig.parameters.values()))
    if first_parm is None and except_if_none is not None:
        raise Exception()
    return first_parm


class Extension:

    def __init__(self, f: FunctionType, f_resolved: FunctionType = None):
        self.f = f
        self.f_resolved = f_resolved

    def __call__(self, *arg, **kwarg):
        return self.resolved(*arg, **kwarg)

    @property
    def resolved(self):
        if self.f_resolved == None:
            if isinstance(self.f, property):
                self.f_resolved = self.f.fget
            else:
                self.f_resolved = self.f
        return self.f_resolved

    @property
    def extended_type(self):
        return self.resolved.__annotations__.get("self")

    @property
    def extension_module(self):
        return self.resolved.__module__

    @property
    def __name__(self):
        return self.resolved.__name__


def extension(f, class_method=True, not_calling_frame=[]):
    """
    Transform a function into a type extension
    #FIXME figure out how to properly handle class vs instance attrs...
    """
    self_parm = first_parm_of(
        f, "A function with no parameters can't be used as a type extension"
    )
    if self_parm.annotation is Parameter.empty:
        raise Exception(
            "A type extension function must include a type annotation for the first parameter"
        )
    target_type = self_parm.annotation
    calling_frame = get_calling_frame_as_import()
    if calling_frame is None:
        # If called from a notebook, looks like a getattr!
        calling_frame = get_calling_frame(not_calling_frame)
    calling_module = calling_frame.f_globals["__name__"]
    if ExtendableType not in target_type.__bases__:
        target_type = replace_with_extendable_type(target_type, calling_module)
    f = Extension(f)
    target_type.__scoped_setattr__(calling_module, f.__name__, f)
    return f


def extension_property(f):
    return extension(property(f))


def class_extension(f):
    """
    Transform a function into a class extension. This is the same as a type
    extension, however, it adds the function as a class method rather than
    an instance method.
    """
    return extension(f, class_method=True)


def class_extension_property(f):
    return extension(property(f), class_method=True)


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
    _scoped_attrs = ModuleScopingDict()
    _attrs_to_modules = dict()
    _methods_by_fullname = dict()

    def match_attr_instance(self, attr_instance):
        return (
            attr_instance is not None
            and isinstance(attr_instance, Extension)
            and isinstance(self, attr_instance.extended_type)
        )

    def find_attr_in_calling_globals(self, attr, calling_frame, calling_module_name):
        resolved_attr = None
        # First, let's see if the name is defined in the module
        calling_module = import_module(calling_module_name)
        resolved_attr = calling_frame.f_globals.get(attr, None)
        if resolved_attr is None or not self.match_attr_instance(resolved_attr):
            resolved_attr = getattr(calling_module, attr, None)
        if not self.match_attr_instance(resolved_attr):
            # Otherwise, look to see if there is a match in any of the known type
            # extension modules that are also imported by the calling frame
            for module in self._attrs_to_modules.get(attr, ()):
                resolved_module = getattr(calling_module, module, None)
                if resolved_module is not None:
                    resolved_attr = getattr(resolved_module, attr, None)
                    if self.match_attr_instance(resolved_attr):
                        break
                    else:
                        resolved_attr = None
                else:
                    resolved_attr = None
        if resolved_attr is not None:
            self.__scoped_setattr__(module, attr, resolved_attr)
        return resolved_attr

    def __getattr__(self, attr):
        calling_frame = get_calling_frame()
        module = calling_frame.f_globals["__name__"]
        resolved_attr = None
        if module in self._scoped_attrs and attr in self._scoped_attrs[module]:
            resolved_attr = self._scoped_attrs[module][attr]
            if not self.match_attr_instance(resolved_attr):
                resolved_attr = None
        if (
            resolved_attr is None
            and module not in self._scoped_attrs
            and attr in self._attrs_to_modules
        ):
            resolved_attr = self.find_attr_in_calling_globals(
                attr, calling_frame, module
            )
        if resolved_attr is None:
            raise AttributeError()
        if isinstance(resolved_attr.f, FunctionType):
            resolved_attr = MethodType(resolved_attr, self)
        elif isinstance(resolved_attr.f, property):
            return resolved_attr.f.fget(self)
        else:
            raise Exception(f"Attribute wasn't a FunctionType, not supported! {attr}")
        return resolved_attr

    @classmethod
    def __scoped_setattr__(cls, module, attr, value):
        cls._scoped_attrs.scoped_setitem(module, attr, value)
        source_modules = cls._attrs_to_modules.setdefault(attr, set())
        source_modules.add(value.extension_module)


def replace_with_extendable_type(target_type, calling_module):
    # generate a safe name for the replacement type
    generate_name = NameGenerator(target_type.__name__)()
    classname = next(generate_name)
    while hasattr(target_type.__module__, classname):
        classname = generate_name()
    scoping_dict = ModuleScopingDict()
    extendable_type = type(classname, (ExtendableType, target_type), dict())
    # replace the target_type with the new extendable_type in the originating module
    # FIXME could we just add the ExtendableType as ... metaclass? or something?
    target_module = import_module(target_type.__module__)
    setattr(target_module, target_type.__name__, extendable_type)
    # replace the target_type with the new extendable type in the calling module, too, if
    # needed
    target_module = import_module(calling_module)
    if (
        hasattr(target_module, target_type.__name__)
        and getattr(target_module, target_type.__name__) is target_type
    ):
        setattr(target_module, target_type.__name__, extendable_type)
    return extendable_type
