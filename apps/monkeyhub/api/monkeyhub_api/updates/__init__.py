"""Desktop updates: preparation, automatic checks and the owned host's activation handshake.

A patch reaches the Hub in one of two ways: a local developer patch the user
uploads, or the unsigned prerelease channel, where the public GitHub releases
of this repository publish an update index naming a delta patch for this exact
installed commit. ``feed`` reads that channel, ``desktop_updates`` stages
either patch by the same path and owns the transaction and its activation, and
``models`` holds the DTOs the update routes answer with and accept. Checksums
and the release manifest detect a changed or foreign download; they do not
establish the publisher (issue #58).
"""
