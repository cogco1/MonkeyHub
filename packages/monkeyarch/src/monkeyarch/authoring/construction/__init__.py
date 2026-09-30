"""Construction first (#419, L1): the agent authors geometry with one bounded script.

A construction script is a small Python subset, interpreted and never executed
(``script``). It manipulates profiles, planes, anchors and shape handles
(``shapes``) through a small generic vocabulary (``vocabulary``). What it leaves
is lowered to ``Component@1``/``Element@1`` rows, where producers are chosen and
nowhere else (``lowering``). ``geometry_view`` reads a record back in the same
construction terms.
"""
