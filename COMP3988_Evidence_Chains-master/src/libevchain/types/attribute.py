'''
    an attribute is a property of an artefact/relationship type that
    can be set by the evidence chain json
'''

def _deco_name_typecheck(t):
    '''
        decorator for filling in typecheck function name
    '''
    def _wrap(*a, **ka):
        res = t(*a, **ka)
        # patches a better name into the typecheck fn
        res.__name__ = f"typecheck<{res._boundtype}>"
        res.__qualname__ = f"typecheck<{res._boundtype}>"

        return res

    return _wrap

_typecheck_any = lambda *a, **ka: True
_typecheck_any._boundtype = "Any"

@_deco_name_typecheck
def _mk_typecheck_primitive(t):
    '''
        given a type, returns a typecheck
        function for that type

        t [type]
            the type to do a typecheck for
    '''
    def _typecheck_fn(o):
        return isinstance(o, t)

    _typecheck_fn._boundtype = str(t)
    return _typecheck_fn


@_deco_name_typecheck
def _mk_typecheck_literal(*lit):
    '''
        given a series of literal values, returns a typecheck
        function matching any of those literals
    '''
    if not lit:
        raise Exception("AttributeType 'Literal' requires at least one literal")

    def _typecheck_fn(o):
        return o in lit

    type_repr = ', '.join(map(repr, lit))
    _typecheck_fn._boundtype = f"literal({type_repr})"
    return _typecheck_fn


@_deco_name_typecheck
def _mk_typecheck_either(*types):
    if len(types) < 2:
        raise Exception("AttributeType 'Either' requires at least two types")

    def _typecheck_fn(o):
        return any(
            map(
                lambda t: t(o),
                types,
            )
        )

    type_repr = ', '.join(map(lambda t: t._boundtype, types))
    _typecheck_fn._boundtype = f"either({type_repr})"
    return _typecheck_fn


@_deco_name_typecheck
def _mk_typecheck_listof(t):
    def _typecheck_fn(o):
        return isinstance(o, list) and all(t(elem) for elem in o)

    _typecheck_fn._boundtype = f"list<{t._boundtype}>"
    return _typecheck_fn


@_deco_name_typecheck
def _mk_typecheck_dictof(t_v):
    def _typecheck_fn(o):
        return (
            isinstance(o, dict) and
            all(t_v(key) for value in o.values())
        )

    _typecheck_fn._boundtype = f"dict<{t_v._boundtype}>"
    return _typecheck_fn


@_deco_name_typecheck
def _mk_typecheck_record(fields):
    def _typecheck_fn(o):
        return (
            isinstance(o, dict) and
            fields.keys() == o.keys() and
            all(
                map(
                    # items() gives a (key, type) tuple
                    lambda kt: kt[1](o[kt[0]]),
                    fields.items(),
                )
            )
        )

    type_repr = ', '.join(map(lambda kt: f"'{kt[0]}': {kt[1]._boundtype}", fields.items()))
    _typecheck_fn._boundtype = f"record({type_repr})"
    return _typecheck_fn


class AttributeTypes():
    '''
        container class for attribute types
        an attribute type should be native to JSON (ie. this ~should be exhaustive)

        these exist only to be passed to Attribute when declaring an attribute
        for an artefact/relationship type

        some types are higher level, and must have the primitive types passed to them
    '''
    # primitive types are usable as is
    Any = _typecheck_any
    Bool = _mk_typecheck_primitive(bool)
    Str = _mk_typecheck_primitive(str)
    Int = _mk_typecheck_primitive(int)
    Float = _mk_typecheck_primitive(float)

    # higher level types should be called
    # like <Type>(<InnerType>)
    ListOf = _mk_typecheck_listof    # checks for a list of the given type
    DictOf = _mk_typecheck_dictof    # checks for a dict of string to
    Either = _mk_typecheck_either    # allows multiple different types
    Literal = _mk_typecheck_literal  # allows matching a set of literal values
    Record = _mk_typecheck_record    # allows matching against a dict with fixed keys and typed entries


    def __init__(self):
        raise Exception("AttributeTypes is a container class and should never be __init__()ed")


class Attribute():
    def __init__(self, typecheck, default):
        '''
            typecheck is a function obj -> bool,
            reporting if that object is of the correct type

            default is a value that is used when an artefact/relationship
            doesn't have this attribute set
            default isn't typechecked, and so can (for example) be None
        '''
        self.typecheck_fn = typecheck
        self.default = default


    def typecheck(self, o):
        '''
            dispatch to typechecker function
        '''
        return self.typecheck_fn(o)
