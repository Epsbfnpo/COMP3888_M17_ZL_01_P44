from libevchain.types.artefact_types import (
    expose_artefact_method,
    register_artefact_type,
    ArtefactType,
)

from libevchain.types.attribute import Attribute, AttributeTypes


@register_artefact_type('text')
class TextType(ArtefactType):
    @staticmethod
    def attributes():
        return {
            'language': Attribute(AttributeTypes.Str, None),
            # TODO : any other attributes that can be shared between text types?
            # maybe encoding?
        }

    @expose_artefact_method
    def load_from_file(self):
        '''
            read all of the text from the file path associated
            with an artefact
        '''
        file_contents = None
        with open(self.get_file(), "r") as f:
            file_contents = f.read()
        return file_contents

    @expose_artefact_method
    def stream_from_file(self):
        '''
            stream the artefact from the file path associated
            with an artefact
        '''
        with open(self.get_file(), "r") as f:
            yield from f
