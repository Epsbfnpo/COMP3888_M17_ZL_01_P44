'''
    an artefact type provides a small method table
    for handling artefacts of a given type
'''

from libevchain.types.attribute import Attribute, AttributeTypes
from libevchain.exceptions import AttributeException

__supported_artefact_types = {}


def expose_artefact_method(fn):
    '''
        when used to decorate a method of an artefact type,
        makes it so that artefacts of the given type inherit that
        method

        note that this turns "self" in the artefact type method
        into a reference to the artefact itself
    '''
    fn.expose_to_artefact = True
    return fn


class ArtefactType():
    '''
        an ArtefactType provides a class-hierarchy style interface
        for artefact evaluation

        note that ArtefactType is only a method table, instances
        do not have any state

        custom pipelines can and should define new types that extend
        the very basic set
    '''
    _artefact_methods: dict = {}
    _current_passes: list = []

    def __init_subclass__(cls):
        '''
            when creating a new ArtefactType,
            bind its new exposed methods as Artefact methods
        '''
        super().__init_subclass__()
        cls._artefact_methods = {}
        cls._current_passes = []
        for name, method in cls.__dict__.items():
            if getattr(method, "expose_to_artefact", False):
                cls._artefact_methods[name] = method


    @classmethod
    def _get_artefact_methods(cls):
        '''
            return a set of methods exposed to the artefact by this class
        '''
        if cls is ArtefactType:
            return {}

        res = {}

        for base in cls.__bases__:
            res |= base._get_artefact_methods()

        res |= cls._artefact_methods

        return res

    def get_artefact_methods(self):
        '''
            dispatch to classmethod
        '''
        return type(self)._get_artefact_methods()


    @classmethod
    def _get_supported_passes(cls):
        if cls is ArtefactType:
            return cls._current_passes

        res = cls._current_passes
        for base in cls.__bases__:
            res = res + base._get_supported_passes()

        return res

    def get_passes(self):
        return type(self)._get_supported_passes()

    # TODO : we need a way to reset supported passes
    # so that state can't linger between a pipeline
    # (ie. pipeline A sets a pass for type T, pipeline B
    # sets a pass only for type T' inheriting from T, at present
    # this means that the pass from pipeline A remains and
    # isn't reset)

    @classmethod
    def _set_supported_passes(cls, passes):
        cls._current_passes = passes

    def set_passes(self, passes):
        type(self)._set_supported_passes(passes)


    def __hash__(self):
        # all instances hash the same,
        # and the type itself hashes the same
        # as the instance
        return hash(type(self))


    def __eq__(self, other):
        return hash(self) == hash(other)


    @classmethod
    def _get_attributes(cls):
        '''
            return a set of attributes supported by this type
            dispatches to user-authored attributes()
        '''
        if cls is ArtefactType:
            return {}

        res = {}

        for base in cls.__bases__:
            res |= base._get_attributes()

        res |= cls.attributes()

        return res


    def get_attributes(self):
        '''
            dispatch to classmethod
        '''
        return type(self)._get_attributes()


    @staticmethod
    def attributes():
        '''
            return a set of strings holding attributes supported
            by this type
        '''
        # ArtefactType natively supports no attributes
        return {}


    def validate_attributes(self, attrs):
        type_attributes = self.get_attributes()
        unknown_attributes = set(attrs.keys()).difference(set(type_attributes))

        if unknown_attributes:
            raise AttributeException(f"Unknown attributes {unknown_attributes} for type {type(self)}")

        for k, v in attrs.items():
            if not type_attributes[k].typecheck(v):
                raise AttributeException(f"'{k}' = {v} does not satisfy typecheck for {type_attributes[k]}")


# special type object that matches *all* artefact types
# (ie. the equivalent of object in the python type system)
ALL_ARTEFACTS = ArtefactType


def get_artefact_type(type_name):
    if type_name in __supported_artefact_types:
        return __supported_artefact_types[type_name]
    raise Exception(f"Unknown artefact type '{type_name}'")


__registration_hooks = []


def hook_artefact_type_registration(fn, retroactive=True):
    if retroactive:
        for artefact_type_instance in __supported_artefact_types.values():
            fn(type(artefact_type_instance))

    __registration_hooks.append(fn)


def register_artefact_type(name, cls=None):
    '''
        split between being a decorator factory
        (if called with no class) or a regular registration
        function

        IN:
            name [str]
                the name of the artefact type to register

            cls [type | None] @OPTIONAL
                if provided, this directly registers that class
                as the artefact type
                otherwise, this creates a class decorator that
                does the registration

        OUT: [fn(type) -> None]
            a decorator that registers a class under the given
            name
            this should only be used if this was called without
            passing in a class (ie. as a decorator)
    '''
    def _wrap(cls):
        if name in __supported_artefact_types:
            raise Exception(f"Duplicate type name '{name}'")
        __supported_artefact_types[name] = cls()
        cls.artefact_type_name = name

        # run hooks
        for hook in __registration_hooks:
            hook(cls)

        return cls

    # if a class was provided, register it immediately
    if cls is not None:
        _wrap(cls)


    # also, expose _wrap so this can be used as a decorator
    return _wrap


def reset_artefact_passes():
    for artefact_type in __supported_artefact_types.values():
        artefact_type.set_passes([])
