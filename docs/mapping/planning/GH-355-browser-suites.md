# GH-355

Issue: https://github.com/cogco1/MonkeyHub/issues/355
Base: `d266d589`.

Repair the local MonkeyHub browser suites that already fail on main (#355): modelSync, boardIntentHandoff, intentViewSource, workModelExport and elevation, and make the suites that need a real isolated Studio API (documentLink, workingCopy, versionNotifications) runnable from written steps or a fixture. Each failure is judged either as a product change the test follows, naming the PR, or as a product regression fixed in the product.
