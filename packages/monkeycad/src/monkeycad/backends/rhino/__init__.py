"""The Rhino compatibility backend.

``script`` translates a compiled program into one rhinoscriptsyntax script, ``export``
runs it in a supervised Rhino 8 COM host and reads the saved ``.3dm`` back,
``step_import`` prepares an editable work model from the exact STEP the OCCT backend
wrote, and ``backend`` registers the export in ``monkeycad.registry``.
"""
