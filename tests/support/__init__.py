"""Fixtures that more than one test root shares, kept once.

A test root cannot import another root's tests, and test-only code does not go
into a production package, so a fixture both a package's tests and the
cross-owner suites need lives here (#534). Nothing here is a test. The root
conftest.py and ``unittest discover -t .`` put the checkout on the path, so a
test imports it as ``tests.support``; production code never does
(governance/architecture_policy.json).
"""
