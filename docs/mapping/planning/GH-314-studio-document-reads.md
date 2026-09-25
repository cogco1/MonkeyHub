# GH-314

Issue: https://github.com/cogco1/MonkeyHub/issues/314
Base: `73711ba0` (batch H1, after GH-244 released).

Studio reads of a run's records stop re-reading the project: listing a run's records reads each file once, and the document list and the runtime's candidate check select the record kinds they use by name.
