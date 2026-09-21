# Evidence Chain JSON Format

Evidence chains can be represented by JSON files, providing a means of serialising them for distribution.

At the top level, the format shall consist of a single JSON object, which must include all of the following fields:
- `artefacts | list<Artefact>`: a list of **Artefact** objects (see below). The order does not matter.
- `final_artefact_hash | str`: a hash (which must match the hash of an element of `artefacts`) of an artefact. The artefact that matches this hash shall be treated as the final artefact (and so its scores will be emitted when evalauting the evidence chain).
- `hash_method | str`: a string representing the method used to compute artefact hashs. See below for a list of valid values.


## Artefact

Artist created artefacts are referenced in evidence chains based on their content.
That is, a hash of the artefact file is used to refer to the file.

An `Artefact` is a JSON object which has the following fields:
- `artefact_hash | str`: a hash of the corresponding artefact file, computed with the top level `hash-method`.
- `artefact_type | str`: a type specification. Valid artefact types depend on the plugins loaded, but a baseline set of standard types is provided below.
- `evidence | list<Evidence>`: a list of Evidence objects (see below) marking other artefacts as evidence for this artefact. The order does not matter.
- `attributes | obj`: an arbitrary JSON object holding attributes for this artefact. The valid attributes are defined by the artefact's type.


## Evidence

Each artefact has a list of evidence entries, describing what artefacts serve as evidence for its human authorship, and how they provide this evidence.

An `Evidence` entry is a JSON object which has the following fields:
- `hash | str`: a hash of the artefact that is claimed to be evidence, computed with the top level `hash-method`.
- `relationship_type | str`: a relationship type specification. Valid relationship types depend on the plugins loaded and the types of artefacts in the relationship, but a baseline set of standard types is provided below.
- `attributes | obj`: an arbitrary JSON object holding attributes for this evidence relationship. The valid attributes are defined by the relationship's type.


## Hash Methods

At present, the only supported `hash-method` is `"sha256"`.


## Artefact Types

While an artefact can technically take any type (provided a plugin is available to handle that artefact type), it is recommended to make use of standard artefact types to ensure plugin compatibility.

Below is listed the standard artefact types, and their standard subtypes.


### Text

The `text` type indicates that an artefact is to be interpreted as a textual document.
Unless a subtype is provided that indicates otherwise, the artefact should be treated as plaintext.
Should it be the case that the artefact is not plaintext, there must be a subtype-bound plugin that exposes the artefact's content as plaintext.

<TODO : `text` subtypes - `script`, etc.>


### <TODO : other artefact types>


## Relationship Types

<TODO : relationship types>
