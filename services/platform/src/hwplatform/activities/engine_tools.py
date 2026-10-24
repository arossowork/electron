"""engine_tool (ADR 0001, 7.6): one engine lookup or command for an agent tool, on the engine (or sim) queue.

The agent worker has no KiCad, ngspice or internet egress. Its MCP tools start an EngineToolWorkflow, which
runs this activity where the tools are:

- `lib_sym`, `lib_show`, `lib_fp`, `lib_pads`, `parts_search`: `hwflow lib ...` / `hwflow parts ...`.
- `trace`, `fw`, `fw_test` (`hwflow fw test`: the firmware host tests with gcc), `floorplan`, `optimize`: the
  command on the workspace tree (materialized in a scratch dir, with the last build's status.json as
  build/status.json); `fw --scaffold` and `optimize --apply` give back
  the tree.
- `fetch_url`: a public http(s) document, at most 20 MiB, stored in the tenant's S3 prefix (with a text
  version of a PDF). Private, loopback, link-local and other non-global addresses are refused, for every
  redirect hop and for the address actually connected to.

Each command runs as `python -m hwflow.cli` in its own process group with a timeout; arguments are passed as a
list (no shell) and validated so that no value can become an option.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import re
import shutil
import signal
import socket
import sys
import uuid
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit

from hwflow.artifacts import get_tree, put_tree
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..contracts import (
    A_ENGINE_TOOL,
    ENGINE_TOOL_DEFAULT_POLICY,
    ENGINE_TOOL_POLICY,
    HEARTBEAT_EVERY_S,
    KILL_GRACE_S,
    EngineToolInput,
    EngineToolOutput,
    errors,
)
from .deps import Deps

TEXT_MAX = 60_000
FETCH_MAX = 20 * 1024 * 1024
FETCH_REDIRECTS = 5
_WORD = re.compile(r"^[^\x00-\x1f]{1,300}$")
_KEY = re.compile(r"^[A-Za-z0-9_.]{1,100}$")
_CATEGORY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _/&().,+-]{0,99}$")
WRITES = {"fw": "scaffold", "optimize": "apply"}


class Refused(ValueError):
    pass


def _word(v, what: str) -> str:
    s = str(v or "").strip()
    if not _WORD.match(s):
        raise Refused(f"{what} must be a text of 1 to 300 characters")
    return s


def argv_for(tool: str, args: dict, proj: str) -> list[str]:
    """The `hwflow` arguments of a tool call. Values that could read as an option come after `--` or as
    `--opt=value`; anything else is refused."""
    if tool == "lib_sym":
        return ["lib", "sym", "--", *_word(args.get("query"), "query").split()]
    if tool == "lib_fp":
        return ["lib", "fp", "--", *_word(args.get("query"), "query").split()]
    if tool == "lib_show":
        return ["lib", "show", "--", _word(args.get("lib_id"), "lib_id")]
    if tool == "lib_pads":
        return ["lib", "pads", "--", _word(args.get("fp_id"), "fp_id")]
    if tool == "parts_search":
        cat = str(args.get("category") or "")
        if not _CATEGORY.match(cat):
            raise Refused("category must be a part category name")
        out = ["parts", cat]
        for k, v in sorted((args.get("filters") or {}).items()):
            if not _KEY.match(str(k)) or "\n" in str(v):
                raise Refused(f"bad filter {k!r}")
            out.append(f"{k}={v}")
        if args.get("text"):
            out.append(f"--text={_word(args['text'], 'text')}")
        out.append(f"--limit={max(1, min(int(args.get('limit') or 15), 50))}")
        return out
    if tool == "trace":
        return ["trace", proj]
    if tool == "floorplan":
        return ["floorplan", proj]
    if tool == "fw_test":
        return ["fw", "test", proj]
    if tool == "fw":
        return ["fw", proj, *(["--scaffold"] if args.get("scaffold") else [])]
    if tool == "optimize":
        out = ["optimize", proj]
        if args.get("budget"):
            out.append(f"--budget={max(1, min(int(args['budget']), 2000))}")
        if args.get("apply"):
            out.append("--apply")
        return out
    raise Refused(f"unknown engine tool {tool!r}")


def _global(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def check_url(url: str, resolve=socket.getaddrinfo) -> str:
    """The URL if it is http(s) on port 80/443 to a host whose every address is public; Refused otherwise."""
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname or u.username or u.password:
        raise Refused("only public http(s) URLs without credentials")
    port = u.port or (443 if u.scheme == "https" else 80)
    if port not in (80, 443):
        raise Refused("only ports 80 and 443")
    try:
        infos = resolve(u.hostname, port, type=socket.SOCK_STREAM)
    except OSError as e:
        raise Refused(f"cannot resolve {u.hostname}") from e
    addrs = {i[4][0] for i in infos}
    if not addrs or not all(_global(a) for a in addrs):
        raise Refused(f"{u.hostname} resolves to a private or reserved address")
    return url


class EngineTools:
    def __init__(self, deps: Deps):
        self.deps = deps

    @activity.defn(name=A_ENGINE_TOOL)
    async def engine_tool(self, inp: EngineToolInput) -> EngineToolOutput:
        try:
            if inp.tool == "fetch_url":
                return await self._fetch(inp)
            return await self._command(inp)
        except Refused as e:
            return EngineToolOutput(text=f"refused: {e}", ok=False)

    # -- commands ----------
    async def _command(self, inp: EngineToolInput) -> EngineToolOutput:
        work = self.deps.scratch() / f"tool-{uuid.uuid4().hex[:12]}"
        proj = work / "proj"
        try:
            argv = argv_for(inp.tool, dict(inp.args), str(proj))
            store = self.deps.store(inp.ctx.tenant_id)
            if inp.workspace is not None:
                await asyncio.to_thread(get_tree, store, inp.workspace, proj)
                if inp.status_json is not None:
                    await asyncio.to_thread(
                        store.get_file, inp.status_json.sha256, proj / "build" / "status.json"
                    )
            work.mkdir(parents=True, exist_ok=True)
            pol = ENGINE_TOOL_POLICY.get(inp.tool, ENGINE_TOOL_DEFAULT_POLICY)
            rc, out = await run_cli(argv, work, limit_s=pol.start_to_close_s - 30)
            text = out.replace(str(proj), ".")
            tree = None
            flag = WRITES.get(inp.tool)
            if flag and inp.args.get(flag) and inp.workspace is not None:

                def include(rel) -> bool:
                    top = PurePosixPath(rel).parts[0]
                    return top not in ("build", ".hwflow-ref", ".claude") and not (proj / rel).is_symlink()

                tree = await asyncio.to_thread(put_tree, store, proj, name="workspace", include=include)
            return EngineToolOutput(text=text, ok=rc == 0, workspace=tree)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # -- fetch_url ----------
    async def _fetch(self, inp: EngineToolInput) -> EngineToolOutput:
        import httpx

        url = check_url(str(inp.args.get("url") or ""))
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=False, headers={"User-Agent": "hwflow-agent/1"}
        ) as c:
            for _ in range(FETCH_REDIRECTS + 1):
                async with c.stream("GET", url) as r:
                    peer = r.extensions.get("network_stream") or None
                    addr = peer.get_extra_info("server_addr") if peer is not None else None
                    if addr and not _global(str(addr[0])):
                        raise Refused("the server address is private or reserved")
                    if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                        url = check_url(urljoin(url, r.headers["location"]))
                        continue
                    if r.status_code != 200:
                        return EngineToolOutput(text=f"HTTP {r.status_code} for {url}", ok=False)
                    if int(r.headers.get("content-length") or 0) > FETCH_MAX:
                        raise Refused("the document is larger than 20 MiB")
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > FETCH_MAX:
                            raise Refused("the document is larger than 20 MiB")
                        activity.heartbeat(len(buf))
                    ctype = (
                        (r.headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
                    )
                    break
            else:
                raise Refused("too many redirects")
        store = self.deps.store(inp.ctx.tenant_id)
        data = bytes(buf)
        files = {"file": await asyncio.to_thread(store.put_bytes, data, media_type=ctype, name="download")}
        note = f"{len(data)} bytes, {ctype}"
        if data[:5] == b"%PDF-" and shutil.which("pdftotext"):
            txt = await pdf_text(data, self.deps.scratch())
            if txt:
                files["text"] = await asyncio.to_thread(
                    store.put_bytes, txt, media_type="text/plain", name="text"
                )
                note += f", text {len(txt)} bytes"
        return EngineToolOutput(text=f"fetched {url}: {note}", ok=True, files=files)


async def pdf_text(data: bytes, scratch: Path) -> bytes | None:
    d = scratch / f"pdf-{uuid.uuid4().hex[:12]}"
    d.mkdir(parents=True)
    try:
        (d / "in.pdf").write_bytes(data)
        p = await asyncio.create_subprocess_exec(
            "pdftotext",
            "-layout",
            str(d / "in.pdf"),
            str(d / "out.txt"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(p.wait(), 120)
        except TimeoutError:
            p.kill()
            return None
        out = d / "out.txt"
        return out.read_bytes() if out.exists() else None
    finally:
        shutil.rmtree(d, ignore_errors=True)


async def run_cli(argv: list[str], cwd: Path, *, limit_s: float) -> tuple[int, str]:
    """`python -m hwflow.cli <argv>` in its own process group, heartbeating; killed on cancel or timeout."""
    p = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "hwflow.cli",
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        env={**os.environ, "HWFLOW_HEADLESS": "1"},
    )
    chunks: list[bytes] = []

    async def read() -> None:
        if p.stdout is None:
            return
        async for line in p.stdout:
            chunks.append(line)
            if sum(map(len, chunks)) > 4 * TEXT_MAX:
                del chunks[1 : len(chunks) // 2]

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_EVERY_S)
            activity.heartbeat((b"".join(chunks[-1:]) or b"").decode("utf-8", "replace")[:300])

    reader, hb = asyncio.create_task(read()), asyncio.create_task(beat())
    try:
        await asyncio.wait_for(asyncio.shield(reader), limit_s)
        rc = await p.wait()
    except (TimeoutError, asyncio.CancelledError) as e:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(p.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(p.wait(), KILL_GRACE_S)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(p.pid, signal.SIGKILL)
        reader.cancel()
        if isinstance(e, asyncio.CancelledError):
            raise
        raise ApplicationError(
            f"{argv[0]} timed out after {limit_s:.0f} s", type=errors.ToolCrashed
        ) from None
    finally:
        hb.cancel()
    text = b"".join(chunks).decode("utf-8", "replace")
    if len(text) > TEXT_MAX:
        text = text[: TEXT_MAX // 2] + "\n...\n" + text[-TEXT_MAX // 2 :]
    return rc, text
