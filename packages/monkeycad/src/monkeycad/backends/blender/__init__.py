"""The Blender backend and the Blender visualization of verified OCCT output.

``backend`` realizes a compiled program as a saved ``.blend`` and reads it back in a fresh
process, ``worker`` is the script it starts inside Blender's own Python, and ``projection``
renders a verified OCCT STEP in Blender.
"""
