"""The runtime's HTTP face: what ``create_app`` mounts and what goes on the wire.

``routes`` serves ``/api``, ``dto`` holds the wire shapes and ``conditional`` answers
conditional reads. The application layer below never imports a route or the
middleware (governance/architecture_policy.json); the record schemas it still reads
through ``dto`` move to it with #519.
"""
