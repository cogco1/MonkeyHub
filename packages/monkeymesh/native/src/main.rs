//! Hub-owned transparent TCP transport. Stdin/stdout carry control only, never HTTP bytes.
use std::{collections::{HashMap, HashSet}, net::SocketAddr, str::FromStr, sync::{Arc, atomic::{AtomicU64, Ordering}}, time::Duration};
use iroh::{endpoint::presets, Endpoint, SecretKey};
use iroh_tickets::endpoint::EndpointTicket;
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::{io::{AsyncBufReadExt, AsyncWriteExt, BufReader}, net::{TcpListener, TcpStream}, sync::{mpsc, oneshot, Mutex, RwLock}};

type Error = Box<dyn std::error::Error + Send + Sync>;
const DATA: &[u8] = b"monkeyhub/tcp/1";
const ENROLL: &[u8] = b"monkeyhub/enroll/1";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Config {
    mode: String,
    secret_key: String,
    target: Option<SocketAddr>,
    enrollment_target: Option<SocketAddr>,
    ticket: Option<String>,
    #[serde(default)] enrollment: bool,
    #[serde(default)] allowed: HashSet<String>,
    #[serde(default)] pairing: bool,
    #[serde(default)] test_loopback: bool,
}
struct Control {
    allowed: RwLock<HashSet<String>>,
    pairing: RwLock<bool>,
    pending: Mutex<HashMap<u64, oneshot::Sender<()>>>,
    sequence: AtomicU64,
    out: mpsc::UnboundedSender<Value>,
}

async fn forward(tcp: TcpStream, mut send: noq::SendStream, mut recv: noq::RecvStream) -> Result<(), Error> {
    let (mut read, mut write) = tcp.into_split();
    // Half close is preserved. No message size limit, whole-body buffering or stream deadline.
    let up = async { tokio::io::copy(&mut read, &mut send).await?; send.finish()?; Ok::<_, Error>(()) };
    let down = async { tokio::io::copy(&mut recv, &mut write).await?; write.shutdown().await?; Ok::<_, Error>(()) };
    tokio::try_join!(up, down)?;
    Ok(())
}

async fn incoming(connection: iroh::endpoint::Connection, config: Arc<Config>, ctl: Arc<Control>) -> Result<(), Error> {
    let peer = connection.remote_id().to_string();
    let enrollment = connection.alpn() == ENROLL;
    if if enrollment { !*ctl.pairing.read().await } else { !ctl.allowed.read().await.contains(&peer) } {
        connection.close(1u32.into(), b"not admitted");
        return Ok(());
    }
    let target = if enrollment { config.enrollment_target } else { config.target }.ok_or("target unavailable")?;
    loop {
        let (send, recv) = match connection.accept_bi().await { Ok(stream) => stream, Err(_) => return Ok(()) };
        if !enrollment && !ctl.allowed.read().await.contains(&peer) {
            connection.close(1u32.into(), b"not admitted"); return Ok(());
        }
        let ctl = ctl.clone(); let peer = peer.clone();
        tokio::spawn(async move {
            let tcp = match TcpStream::connect(target).await { Ok(tcp) => tcp, Err(_) => return };
            let port = match tcp.local_addr() { Ok(addr) => addr.port(), Err(_) => return };
            let id = ctl.sequence.fetch_add(1, Ordering::Relaxed);
            let (ack, wait) = oneshot::channel();
            ctl.pending.lock().await.insert(id, ack);
            let _ = ctl.out.send(json!({"event":"peer", "id":id, "peer":peer, "port":port, "enrollment":enrollment}));
            // Parent installs a trusted source-port binding before any HTTP byte can arrive.
            if matches!(tokio::time::timeout(Duration::from_secs(10), wait).await, Ok(Ok(()))) {
                let _ = forward(tcp, send, recv).await;
            }
            ctl.pending.lock().await.remove(&id);
            let _ = ctl.out.send(json!({"event":"peerClosed", "id":id, "port":port}));
        });
    }
}

async fn run() -> Result<(), Error> {
    let mut input = BufReader::new(tokio::io::stdin()).lines();
    let first = input.next_line().await?.ok_or("configuration missing")?;
    if first.len() > 128 * 1024 { return Err("configuration too large".into()); }
    let config: Config = serde_json::from_str(&first)?;
    for addr in [config.target, config.enrollment_target].into_iter().flatten() {
        if !addr.ip().is_loopback() || addr.port() == 0 { return Err("target must be loopback".into()); }
    }
    if config.mode != "listen" && config.mode != "connect" { return Err("invalid mode".into()); }
    let secret = SecretKey::from_str(&config.secret_key)?;
    let mut builder = Endpoint::builder(presets::N0).secret_key(secret).alpns(vec![DATA.to_vec(), ENROLL.to_vec()]);
    // Explicit isolated-test mode never reaches a public relay. Production uses N0 defaults.
    if config.test_loopback { builder = builder.relay_mode(iroh::RelayMode::Disabled).clear_address_lookup().bind_addr("127.0.0.1:0".parse::<SocketAddr>()?)?; }
    let endpoint = builder.bind().await?;
    if !config.test_loopback { let _ = tokio::time::timeout(Duration::from_secs(5), endpoint.online()).await; }
    let (out, mut output) = mpsc::unbounded_channel::<Value>();
    let writer = tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(value) = output.recv().await {
            let mut line = serde_json::to_vec(&value).unwrap(); line.push(b'\n');
            if stdout.write_all(&line).await.is_err() || stdout.flush().await.is_err() { break; }
        }
    });
    let ctl = Arc::new(Control { allowed: RwLock::new(config.allowed.clone()), pairing: RwLock::new(config.pairing), pending: Mutex::new(HashMap::new()), sequence: AtomicU64::new(1), out });
    let config = Arc::new(config);
    let listener = if config.mode == "connect" { Some(TcpListener::bind("127.0.0.1:0").await?) } else { None };
    ctl.out.send(json!({"event":"ready", "nodeId":endpoint.id().to_string(), "ticket":EndpointTicket::new(endpoint.addr()).to_string(), "port":listener.as_ref().map(|s| s.local_addr().unwrap().port())}))?;
    let serve = if let Some(listener) = listener {
        let address = EndpointTicket::from_str(config.ticket.as_deref().ok_or("ticket missing")?)?.endpoint_addr().clone();
        let endpoint = endpoint.clone(); let alpn = if config.enrollment { ENROLL } else { DATA };
        let active = Arc::new(Mutex::new(None::<iroh::endpoint::Connection>));
        tokio::spawn(async move {
            while let Ok((tcp, _)) = listener.accept().await {
                let endpoint = endpoint.clone(); let address = address.clone(); let active = active.clone();
                tokio::spawn(async move {
                    // Retry belongs to the HTTP client. A failed stream is never replayed here.
                    let connection = {
                        let mut selected = active.lock().await;
                        if selected.as_ref().is_some_and(|connection| connection.close_reason().is_some()) {
                            *selected = None;
                        }
                        if selected.is_none() {
                            match endpoint.connect(address, alpn).await {
                                Ok(connection) => *selected = Some(connection),
                                Err(_) => return,
                            }
                        }
                        selected.as_ref().unwrap().clone()
                    };
                    if let Ok((send, recv)) = connection.open_bi().await { let _ = forward(tcp, send, recv).await; }
                });
            }
        })
    } else {
        let endpoint = endpoint.clone(); let ctl = ctl.clone(); let config = config.clone();
        tokio::spawn(async move {
            while let Some(accepting) = endpoint.accept().await {
                let ctl = ctl.clone(); let config = config.clone();
                tokio::spawn(async move { if let Ok(connection) = accepting.await { let _ = incoming(connection, config, ctl).await; } });
            }
        })
    };
    while let Some(line) = input.next_line().await? {
        if line.len() > 128 * 1024 { break; }
        let value: Value = match serde_json::from_str(&line) { Ok(value) => value, Err(_) => break };
        match value.get("command").and_then(Value::as_str) {
            Some("stop") => break,
            Some("ack") => { if let Some(id) = value.get("id").and_then(Value::as_u64) { if let Some(ack) = ctl.pending.lock().await.remove(&id) { let _ = ack.send(()); } } }
            Some("admission") => {
                let allowed: HashSet<String> = serde_json::from_value(value["allowed"].clone())?;
                *ctl.allowed.write().await = allowed;
                *ctl.pairing.write().await = value["pairing"].as_bool().unwrap_or(false);
            }
            _ => break,
        }
    }
    serve.abort(); endpoint.close().await; drop(ctl); writer.abort();
    Ok(())
}

#[tokio::main]
async fn main() {
    if run().await.is_err() { eprintln!("MESH_UNAVAILABLE"); std::process::exit(1); }
}
