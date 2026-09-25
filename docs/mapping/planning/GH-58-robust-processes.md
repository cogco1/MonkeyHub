# GH-58 robust-processes

Issue: https://github.com/cogco1/MonkeyHub/issues/58
Base: `acb391ef` (batch DE).

No Hub child process inherits the Hub's stdin, and an interrupted operation with no way to recover is reported as such and can be acknowledged.

Batch F (2026-09-25 afternoon); none touches App.tsx, the i18n catalogs (GH-244) or GH-66's files.
