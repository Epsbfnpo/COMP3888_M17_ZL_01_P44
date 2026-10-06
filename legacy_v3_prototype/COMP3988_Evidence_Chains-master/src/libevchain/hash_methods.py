'''
    provides default hash methods, plus a table of available
    hash methods
'''

import hashlib

# module local table, exposed via below functions
__available_hash_methods = {}

def get_hash_method(method_name: str):
    '''
        attempts to get a hash method with
        the given name
        returns None if there is no such hash method
    '''
    return __available_hash_methods.get(method_name, None)


def add_hash_method(method_name: str, hash_method):
    '''
        attempts to register a new hash method
        will raise an exception if the name collides

        hash_method should be a function for computing the
        hash of a file, returning a string, and should accept
        an open file object (as returned by open() or similar)
        as input
    '''
    if method_name in __available_hash_methods:
        raise Exception(f"Duplicate hash method name {method_name}")

    __available_hash_methods[method_name] = hash_method


def _hash_method(method_name):
    '''
        internal decorator for easily registering
        default hash methods
    '''
    def _wrap(hash_method):
        add_hash_method(method_name, hash_method)
        return hash_method
    return _wrap


# default hash methods

# sha256
@_hash_method("sha256")
def sha256_file_hash(file_handle):
    return hashlib.file_digest(file_handle, "sha256").hexdigest()
