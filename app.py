#!/usr/bin/env python3
"""ACSM de-DRM web UI. Upload an .acsm, get an EPUB."""
import base64
import io
import os
from pathlib import Path

import click
import requests
from flask import Flask, jsonify, render_template_string, request, send_from_directory
from flask_cors import CORS
from loguru import logger
from LibbyDL.DeDRM.dedrm_acsm import dedrm

ROOT = Path(__file__).resolve().parent
BOOKS = ROOT / "books"
BOOKS.mkdir(exist_ok=True)

# ─── de-DRM ────────────────────────────────────────────────────────────────
def dedrm_bytes(data: bytes):
    """Run the de-DRM pipeline on ACSM bytes."""
    dedrm(io.BytesIO(data), str(BOOKS) + "/")


def dedrm_source(source: str):
    """Fetch from URL or read from local path, then de-DRM."""
    if source.startswith(("http://", "https://")):
        r = requests.get(source, timeout=60)
        r.raise_for_status()
        dedrm_bytes(r.content)
    else:
        with open(source, "rb") as f:
            dedrm_bytes(f.read())


# ─── flask app ─────────────────────────────────────────────────────────────
app = Flask(__name__)
CORS(app)

# LH-2026
@app.get("/")
def index():
    return render_template_string(HTML)


@app.get("/api/status")
def api_status():
    """Report whether ADE has been provisioned (keys/decryption.der exists)."""
    key_file = ROOT / "keys" / "decryption.der"
    return jsonify(ade_provisioned=key_file.exists())


@app.post("/api/dedrm")
def api_dedrm():
    """Accept a base64 ACSM upload or a URL/path in `source`."""
    body = request.get_json(force=True) or {}

    if body.get("content_b64"):
        try:
            data = base64.b64decode(body["content_b64"])
        except Exception as e:
            return jsonify(error=f"bad base64: {e}"), 400
        try:
            dedrm_bytes(data)
            return jsonify(ok=True)
        except Exception as e:
            logger.exception("dedrm failed (upload)")
            return jsonify(error=str(e)), 500

    source = (body.get("source") or "").strip()
    if not source:
        return jsonify(error="content_b64 or source required"), 400
    try:
        dedrm_source(source)
        return jsonify(ok=True)
    except Exception as e:
        logger.exception("dedrm failed (source)")
        return jsonify(error=str(e)), 500


@app.get("/api/books")
def api_books():
    return jsonify(files=sorted(p.name for p in BOOKS.iterdir() if p.is_file()))


@app.get("/books/<path:name>")
def api_book_file(name):
    return send_from_directory(BOOKS, name, as_attachment=True)


# ─── cli ───────────────────────────────────────────────────────────────────
@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)


@cli.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=5000, type=int)
@click.option("--cert", default=None, help="Path to cert.pem")
@click.option("--key", default=None, help="Path to key.pem")
def serve(host, port, cert, key):
    """Run the web GUI (HTTPS if cert/key given, otherwise HTTP)."""
    if cert and key:
        ssl_ctx = (cert, key)
    elif cert or key:
        raise click.UsageError("Provide both --cert and --key, or neither.")
    else:
        ssl_ctx = None
    scheme = "https" if ssl_ctx else "http"
    logger.info(f"Open {scheme}://{host}:{port}")
    app.run(host=host, port=port, debug=False, ssl_context=ssl_ctx)


@cli.command(name="dedrm")
@click.argument("source")
def dedrm_cmd(source):
    """De-DRM an ACSM from a URL or local path into books/."""
    dedrm_source(source)
    click.echo(f"Saved to {BOOKS}/")


@cli.command(name="provision-ade-account")
def provision_ade_account():
    """Create/activate a local Adobe ADE account. Run once."""
    from LibbyDL.DeDRM.libadobeAccount import (
        createDeviceFile, createUser, signIn, activateDevice,
        exportAccountEncryptionKeyDER,
    )
    from LibbyDL.DeDRM.libadobe import createDeviceKeyFile, KEY_FOLDER
    from LibbyDL.DeDRM.dedrm_acsm import DECRYPTION_KEY

    os.makedirs(KEY_FOLDER, exist_ok=True)
    createDeviceKeyFile()
    if not createDeviceFile(True, 1):
        raise click.ClickException("device file failed")
    ok, resp = createUser(1, None)
    if not ok:
        raise click.ClickException(f"register: {resp}")
    ok, resp = signIn("anonymous", "", "")
    if not ok:
        raise click.ClickException(f"login: {resp}")
    ok, resp = activateDevice(1, None)
    if not ok:
        raise click.ClickException(f"activate: {resp}")
    if not exportAccountEncryptionKeyDER(DECRYPTION_KEY):
        raise click.ClickException("key export failed")
    click.echo("ADE provisioned.")


# ─── html ──────────────────────────────────────────────────────────────────
HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>ACSM de-DRM</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
  h1 { margin-top: 0; }
  .tabs { display: flex; gap: .5rem; border-bottom: 1px solid #8884; margin-bottom: 1rem; }
  .tabs button { padding: .5rem 1rem; border: none; background: none; cursor: pointer;
                 border-bottom: 2px solid transparent; }
  .tabs button.active { border-bottom-color: #4c8; font-weight: 600; }
  .panel { display: none; } .panel.active { display: block; }
  .row { display: flex; gap: .5rem; margin: .5rem 0; align-items: center; flex-wrap: wrap; }
  button.primary { padding: .45rem .9rem; cursor: pointer; }
  .muted { opacity: .7; font-size: .85rem; }
  .err { color: #c33; }
  ul { padding-left: 1.2rem; }
  .banner { padding: .6rem .9rem; border-radius: 6px; margin-bottom: 1rem; font-size: .9rem; }
  .banner.warn { background: #ffc9; border: 1px solid #cc0; }
  .banner.ok { background: #cfc9; border: 1px solid #4c8; }
</style>
</head>
<body>
<h1>ACSM de-DRM</h1>

<div id="ade-banner" class="banner warn" style="display:none">
  ADE not provisioned. Run: <code>python app.py provision-ade-account</code>
</div>

<div class="tabs">
  <button data-tab="upload" class="active">Upload</button>
  <button data-tab="files">Files</button>
</div>

<section class="panel active" id="tab-upload">
  <p class="muted">
    Grab the <code>.acsm</code> file from libbyapp.com (or any Adobe-fulfilled source)
    and pick it here.
  </p>
  <div class="row">
    <input type="file" id="acsm-file" accept=".acsm">
    <button class="primary" onclick="uploadAcsm()">Upload &amp; de-DRM</button>
  </div>
  <div class="muted" id="upload-msg"></div>
</section>

<section class="panel" id="tab-files">
  <div class="row"><button class="primary" onclick="loadFiles()">Refresh</button></div>
  <ul id="files"></ul>
</section>

<script>
const $ = s => document.querySelector(s);
const api = async (path, opts={}) => {
  const r = await fetch(path, {headers:{'Content-Type':'application/json'}, ...opts});
  const t = await r.text();
  try { return JSON.parse(t); } catch { return {raw:t}; }
};

document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(x => x.classList.remove('active'));
  b.classList.add('active');
  $('#tab-' + b.dataset.tab).classList.add('active');
  if (b.dataset.tab === 'files') loadFiles();
});

async function checkAde() {
  const s = await api('/api/status');
  const banner = $('#ade-banner');
  if (s.ade_provisioned) {
    banner.style.display = 'none';
  } else {
    banner.style.display = 'block';
  }
}

async function uploadAcsm() {
  const input = $('#acsm-file');
  const msg = $('#upload-msg');
  if (!input.files.length) { msg.textContent = 'Pick a file first.'; return; }

  const file = input.files[0];
  msg.textContent = `Reading ${file.name} (${file.size} bytes)…`;

  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const b64 = btoa(bin);

  msg.textContent = `Uploading ${b64.length} b64 chars…`;
  const r = await api('/api/dedrm', {
    method: 'POST',
    body: JSON.stringify({ filename: file.name, content_b64: b64 }),
  });
  if (r.error) { msg.textContent = 'Error: ' + r.error; return; }
  msg.textContent = 'Done — see Files tab.';
  input.value = '';
  loadFiles();
}

async function loadFiles() {
  const r = await api('/api/books');
  const ul = $('#files'); ul.innerHTML = '';
  for (const f of r.files) {
    const li = document.createElement('li');
    li.innerHTML = `<a href="/books/${encodeURIComponent(f)}">${f}</a>`;
    ul.appendChild(li);
  }
}

checkAde();
loadFiles();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    cli()
