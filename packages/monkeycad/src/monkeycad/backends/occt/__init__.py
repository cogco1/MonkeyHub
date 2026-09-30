"""In-process OCCT backend (P107, lane A).

The Rhino path translates a compiled program into a script and lets an
external host realize it.  This backend interprets the same
``CompiledGeometryProgram`` directly against Open CASCADE (OCCT) through the
``cadquery-ocp`` binding: every accepted operation kind maps to one kernel
call, the result is measured in process, written to STEP as exact B-rep, and
tessellated for a viewer-readable mesh ``.3dm`` from the same model.

The kernel-facing mechanics each have a module: loading the binding and the
one-time coordinate mapping (``kernel``), building shapes (``build``, with
``boolean`` planning differences and intersections), measuring them
(``measure``), writing and cold-reading STEP (``step``), reading a native
``.3dm`` (``native_models``), tessellating and writing the mesh preview
(``preview``), and extracting named orthographic visible/hidden polylines
(``projection``) and cut sections and section perspectives (``section``) from
the same B-reps; the errors they raise are in ``errors``.  None of them knows a
project, a run or a workspace rule.  ``cadquery-ocp`` is imported lazily:
importing this package or any of its modules never loads OCCT.
"""
