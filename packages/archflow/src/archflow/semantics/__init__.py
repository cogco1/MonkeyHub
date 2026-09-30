"""archflow.semantics: the architectural vocabulary canonical state may use.

Two tables and one resolver. ``roles`` says what a component does; ``conditions``
says what spatial condition it forms. An entity (a wall, a column, a space) is
neither: entity schemas live in the State Record. Natural language enters only
as an alias that resolves to a registered id; a semantic string that resolves
to nothing is refused by the record with the nearest registered ids named.
"""
