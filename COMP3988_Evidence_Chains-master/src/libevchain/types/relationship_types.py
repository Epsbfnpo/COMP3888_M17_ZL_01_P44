from libevchain.types import artefact_types
from libevchain.exceptions import AttributeException
from libevchain.types.artefact_types import hook_artefact_type_registration, get_artefact_type

__supported_relationship_types = {}


class RelationshipType():
    '''
        similar to ArtefactType, but with no need for metaclassing for
        custom interface inheritance
    '''
    _current_passes: list = []

    def validate_types(artefact_type, evidence_type):
        '''
            determine if some artefact and evidence types
            are supported by this relationship
        '''
        return False


    @classmethod
    def _get_supported_passes(cls):
        if cls is RelationshipType:
            return cls._current_passes

        res = cls._current_passes
        for base in cls.__bases__:
            res = res + base._get_supported_passes()

        return res

    def get_passes(self):
        return type(self)._get_supported_passes()


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
        if cls is RelationshipType:
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
        return {}


    def validate_attributes(self, attrs):
        type_attributes = self.get_attributes()
        unknown_attributes = set(attrs.keys()).difference(set(type_attributes))

        if unknown_attributes:
            raise AttributeException(f"Unknown attributes {unknown_attributes} for type {type(self)}")

        for k, v in attrs.items():
            if not type_attributes[k].typecheck(v):
                raise AttributeException(f"'{k}' = {v} does not satisfy typecheck for {type_attributes[k]}")


def _mk_init_subclass_for_generic_t(generic_t):
    def _generic_t_init_subclass(cls):
        # we can't call super() here since this isn't a class cell
        # (but that should be fine?)
        cls._type_cache = {}
        cls._parent_generic = generic_t

        cls.__init_subclass__ = classmethod(_mk_init_subclass_for_generic_t(cls))

        hook_artefact_type_registration(cls.register_for_type)

    return _generic_t_init_subclass


class GenericRelationshipTypeMetaclass(type):
    def __getattr__(cls, attrname):
        try:
            artefact_type = get_artefact_type(attrname)
            return cls._for_artefact_type(type(artefact_type))
        except:
            raise AttributeError(f"Could not find type {attrname} to specialise relationship {cls} over")


class GenericRelationshipType(metaclass=GenericRelationshipTypeMetaclass):
    '''
        a relationship that is abstract over other type(s)
        almost all GenericRelationshipTypes will end up being
        metaclasses that bind subtypes under a dotted type name
    '''
    _type_cache = {}


    def __init__(self):
        raise Exception("GenericRelationshipTypes should not be initialised")

    def __init_subclass__(cls):
        super().__init_subclass__()
        cls._type_cache = {}
        cls._parent_generic = GenericRelationshipType

        cls.__init_subclass__ = classmethod(_mk_init_subclass_for_generic_t(cls))

        hook_artefact_type_registration(cls.register_for_type)

    @classmethod
    def for_artefact_type(cls, t):
        if cls is GenericRelationshipType:
            raise Exception("GenericRelationshipType shouldn't be specialised")

        cls_parent = cls._parent_generic

        if cls_parent is GenericRelationshipType:
            cls_parent = RelationshipType
        else:
            cls_parent = cls_parent._for_artefact_type(t)

        class _SpecialisedType(cls_parent):
            _bound_type = t
            def validate_types(self, artefact_type, evidence_type):
                return (
                    t == artefact_type and
                    t == evidence_type
                )

            attributes = cls.attributes

        _SpecialisedType.__name__ = f"{cls.__name__}<{t.artefact_type_name}>"
        _SpecialisedType.__qualname__ = f"{cls.__name__}<{t.artefact_type_name}>"

        return _SpecialisedType


    @classmethod
    def register_for_type(cls, t):
        register_relationship_type(f"{cls.relationship_type_name}.{t.artefact_type_name}", cls._for_artefact_type(t))

    @classmethod
    def _for_artefact_type(cls, t):
        if t in cls._type_cache:
            return cls._type_cache[t]

        new_t = cls.for_artefact_type(t)
        cls._type_cache[t] = new_t
        return new_t

    @staticmethod
    def attributes():
        return {}


# special type object that matches *all* relationship types
ALL_RELATIONSHIPS = RelationshipType


def get_relationship_type(type_name):
    if type_name in __supported_relationship_types:
        return __supported_relationship_types[type_name]
    raise Exception(f"Unknown relationship type '{type_name}'")

def register_relationship_type(name, cls=None):
    '''
        split between being a decorator factory
        (if called with no class) or a regular registration
        function

        IN:
            name [str]
                the name of the relationship type to register

            cls [type | None] @OPTIONAL
                if provided, this directly registers that class
                as the relationshup type
                otherwise, this creates a class decorator that
                does the registration

        OUT: [fn(type) -> None]
            a decorator that registers a class under the given
            name
            this should only be used if this was called without
            passing in a class (ie. as a decorator)
    '''
    def _wrap(cls):
        if name in __supported_relationship_types:
            raise Exception(f"Duplicate type name '{name}'")
        __supported_relationship_types[name] = cls()
        cls.relationship_type_name = name
        return cls

    # if a class was provided, register it immediately
    if cls is not None:
        _wrap(cls)

    # also, expose _wrap so this can be used as a decorator
    return _wrap


def reset_relationship_passes():
    for relationship_type in __supported_relationship_types.values():
        relationship_type.set_passes([])
