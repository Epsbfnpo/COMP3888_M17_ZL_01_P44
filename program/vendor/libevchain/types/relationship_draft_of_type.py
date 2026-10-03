from libevchain.types.relationship_types import (
    GenericRelationshipType,
    register_relationship_type,
)


class DraftOf(GenericRelationshipType):
    relationship_type_name = "draft-of"
