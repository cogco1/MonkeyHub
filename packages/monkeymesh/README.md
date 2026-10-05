# monkeymesh

Hub-owned Iroh transport for unchanged HTTP/SSE streams. The native process has
no project API, file store, generic network proxy target, or standalone launcher.
Hub supplies the stable private device key through stdin and binds every incoming
stream's peer identity before acknowledging forwarding into its restricted
loopback member ingress. One reused QUIC connection carries independent TCP
streams. TCP half-close, long event streams and streamed bodies are preserved.

Build from the official pinned Rust toolchain and checked-in dependency lock:

    cargo build --locked --manifest-path packages/monkeymesh/native/Cargo.toml
    python -m pytest packages/monkeymesh/tests -q

Production uses Iroh's N0 discovery and direct/relay defaults. Explicit fixture
mode disables public relay and address discovery and binds loopback only.
No credential is passed on a command line or printed on failure. The process
owns only its supplied transport and shuts down on the Hub's stdin stop/EOF.

The tests stream 200 MiB, check immediate SSE frames, interrupt a connection,
and reconnect with the same synthetic device identity. They are local transport
checks, not different-network or two-physical-machine product acceptance.
Hub membership and local-replica operation are documented in
[project teams](../../docs/development/project-teams.md).
